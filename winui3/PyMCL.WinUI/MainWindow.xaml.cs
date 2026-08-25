using Microsoft.UI;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;
using PyMCL.Models;
using PyMCL.Pages;
using PyMCL.Services;
using Windows.UI;
using WinRT.Interop;

namespace PyMCL;

public sealed partial class MainWindow : Window
{
    private LaunchPage? _launch;
    private InstancePage? _instance;
    private AccountPage? _account;
    private MultiplayerPage? _multiplayer;
    private ServersPage? _servers;
    private PlaytimePage? _playtime;
    private DownloadHubPage? _download;
    private AiPage? _ai;
    private FeedbackPage? _feedback;
    private SettingsPage? _settings;
    private TasksPage? _tasks;
    private string _current = "launch";
    private bool _navLock;
    private int _navGen;
    private readonly Dictionary<string, string> _dockActive = new();
    private bool _dockExpanded;
    private readonly SemaphoreSlim _dockLock = new(1, 1);

    public MainWindow()
    {
        InitializeComponent();
        Title = "PyMCL 启动器 WinUI 3";
        AppServices.Dispatcher = DispatcherQueue;
        AppServices.Toast = ShowToast;
        AppServices.OpenDownload = OpenDownload;
        AppServices.FlyToTasks = FlyToTasks;
        ApplyChrome();
        if (Content is FrameworkElement root)
            root.Loaded += OnLoaded;
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    /// <summary>当前窗口的 DPI 缩放比（96 DPI = 1.0）。</summary>
    private double WindowScale()
    {
        try
        {
            var dpi = GetDpiForWindow(AppServices.WindowHandle);
            if (dpi >= 48) return dpi / 96.0;
        }
        catch { }
        return 1.0;
    }

    private void ApplyChrome()
    {
        try
        {
            var hwnd = WindowNative.GetWindowHandle(this);
            AppServices.WindowHandle = hwnd;
            var id = Win32Interop.GetWindowIdFromWindow(hwnd);
            var app = AppWindow.GetFromWindowId(id);
            // AppWindow.Resize 收的是物理像素。以前直接写死 1180x760，在 150% 缩放的屏幕上
            // 只有 787x507 DIP，窗口明显偏小、内容挤成一团。这里按实际 DPI 折算。
            var scale = WindowScale();
            app.Resize(new Windows.Graphics.SizeInt32(
                (int)Math.Round(1180 * scale), (int)Math.Round(760 * scale)));
            ExtendsContentIntoTitleBar = true;
            SetTitleBar(AppTitleBar);
            var tb = app.TitleBar;
            tb.ExtendsContentIntoTitleBar = true;
            tb.ButtonBackgroundColor = Colors.Transparent;
            tb.ButtonInactiveBackgroundColor = Colors.Transparent;
            tb.ButtonHoverBackgroundColor = Color.FromArgb(24, 0, 0, 0);
            tb.ButtonPressedBackgroundColor = Color.FromArgb(48, 0, 0, 0);
            UpdateTitleBarInset();
        }
        catch { }

        try
        {
            if (MicaController.IsSupported())
                SystemBackdrop = new MicaBackdrop { Kind = MicaKind.Base };
            else if (DesktopAcrylicController.IsSupported())
                SystemBackdrop = new DesktopAcrylicBackdrop();
        }
        catch { }
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        try
        {
            AppServices.Host = await BridgeHost.StartAsync();
            AppServices.Client = AppServices.Host.Client;
            AppServices.Client.EventReceived += OnBridgeEvent;
            AppServices.Client.EventStreamStateChanged += OnEventStreamStateChanged;
            _launch = new LaunchPage();
            _instance = new InstancePage();
            _account = new AccountPage();
            _multiplayer = new MultiplayerPage();
            _servers = new ServersPage();
            _playtime = new PlaytimePage();
            _download = new DownloadHubPage();
            _ai = new AiPage();
            _feedback = new FeedbackPage();
            _settings = new SettingsPage();
            _tasks = new TasksPage();
            NavView.SelectedItem = NavView.MenuItems[0];
            SwapPage(_launch);
            await _launch.ReloadAsync();
            _ = BootExtras();
        }
        catch (Exception ex)
        {
            ShowToast("启动失败", ex.Message, InfoBarSeverity.Error);
            ContentFrame.Content = new TextBlock
            {
                Text = "无法连接后端：\n" + ex.Message,
                Margin = new Thickness(28),
                TextWrapping = TextWrapping.Wrap,
            };
        }
    }

    private bool _dockSizing;

    private void Nav_PaneClosing(NavigationView sender, NavigationViewPaneClosingEventArgs args)
    {
        args.Cancel = true;
    }

    private void ContentRoot_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        // 跨屏拖动导致 DPI 变化时也会走到这里，顺便重算标题栏让位宽度。
        UpdateTitleBarInset();
        if (FlyLayer != null)
        {
            FlyLayer.Width = e.NewSize.Width;
            FlyLayer.Height = e.NewSize.Height;
        }
        if (_dockSizing) return;
        var next = Math.Clamp(Math.Max(0, e.NewSize.Width - 32), 1, 640);
        if (Math.Abs(DockHost.Width - next) < 0.5) return;
        _dockSizing = true;
        DockHost.Width = next;
        _dockSizing = false;
    }

    /// <summary>
    /// 标题栏右侧要给系统的最小化/最大化/关闭三颗按钮让位。以前 XAML 里硬编码
    /// Padding="0,0,138,0"，那是 100% 缩放下的经验值：放大后让不够、标题被按钮压住，
    /// 缩小后又空出一大块。改成读系统给出的 RightInset（物理像素）再折算成 DIP。
    /// </summary>
    private void UpdateTitleBarInset()
    {
        try
        {
            var app = AppWindow.GetFromWindowId(Win32Interop.GetWindowIdFromWindow(AppServices.WindowHandle));
            var inset = app.TitleBar.RightInset;
            if (inset <= 0) return;
            var right = inset / WindowScale();
            if (Math.Abs(AppTitleBar.Padding.Right - right) < 0.5) return;
            AppTitleBar.Padding = new Thickness(0, 0, right, 0);
        }
        catch { }
    }

    private void Nav_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (_navLock) return;
        if (args.SelectedItem is NavigationViewItem item && item.Tag is string key)
            Navigate(key);
    }

    public void Navigate(string key)
    {
        _current = key;
        FrameworkElement? page = key switch
        {
            "launch" => _launch,
            "instance" => _instance,
            "account" => _account,
            "multiplayer" => _multiplayer,
            "servers" => _servers,
            "playtime" => _playtime,
            "download" => _download,
            "ai" => _ai,
            "feedback" => _feedback,
            "settings" => _settings,
            "tasks" => _tasks,
            _ => null,
        };
        var item = FindNav(key);
        if (item != null && !Equals(NavView.SelectedItem, item))
        {
            _navLock = true;
            NavView.SelectedItem = item;
            _navLock = false;
        }
        if (page != null)
            SwapPage(page);
        PlaceDock();
        _ = ReloadCurrentAsync();
    }

    private async void SwapPage(FrameworkElement page)
    {
        if (ReferenceEquals(ContentFrame.Content, page) && page.Opacity >= 0.99)
            return;
        var gen = ++_navGen;
        UIElement? old = ContentFrame.Content as UIElement;
        if (old != null && !ReferenceEquals(old, page) && Motion.AnimationsWanted())
        {
            var outTask = Motion.PageOutAsync(old);
            await Task.WhenAny(outTask, Task.Delay(90));
        }
        else if (old != null && !ReferenceEquals(old, page))
            Motion.ResetVisual(old);
        if (gen != _navGen)
        {
            Motion.ResetVisual(old);
            return;
        }
        ContentFrame.Content = page;
        if (Motion.AnimationsWanted())
            await Motion.PageInAsync(page);
        else
            Motion.ResetVisual(page);
        if (gen != _navGen)
            Motion.ResetVisual(page);
    }

    public async void FlyToTasks(FrameworkElement? source, string text, string? colorHex = null)
    {
        try
        {
            if (AppServices.Client is null || FlyLayer is null) return;
            var s = await AppServices.Client.CallAsync<SettingsDto>("get_settings");
            if (s is not null && !s.UiFlyAnimation) return;
            var duration = s?.UiFlyDurationMs is > 0 ? s.UiFlyDurationMs : 620;
            var tasksItem = FindNav("tasks");
            if (tasksItem is null || source is null) return;
            var color = FlyAnim.ParseColor(colorHex, Color.FromArgb(255, 46, 155, 107));
            var letter = string.IsNullOrWhiteSpace(text) ? "↓" : text.Trim()[..1];
            FlyAnim.FlyTo(FlyLayer, source, tasksItem, letter, color, duration, () =>
            {
                _ = Motion.PulseOnceAsync(TaskBadge);
            });
        }
        catch { }
    }

    private NavigationViewItem? FindNav(string key)
    {
        foreach (var o in NavView.MenuItems.Concat(NavView.FooterMenuItems))
        {
            if (o is NavigationViewItem n && n.Tag as string == key)
                return n;
        }
        return null;
    }

    public void OpenDownload(string? cat = null)
    {
        Navigate("download");
        if (cat != null)
            _download?.ShowCategory(cat);
    }

    private async Task ReloadCurrentAsync()
    {
        try
        {
            if (_current == "launch") await (_launch?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "instance") await (_instance?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "account") await (_account?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "multiplayer") await (_multiplayer?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "servers") await (_servers?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "playtime") await (_playtime?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "download") _download?.ReloadCurrent();
            else if (_current == "ai") await (_ai?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "feedback") await (_feedback?.ReloadAsync() ?? Task.CompletedTask);
            else if (_current == "settings") await (_settings?.ReloadAsync() ?? Task.CompletedTask);
        }
        catch { }
    }

    private bool _sseWasConnected;

    /// <summary>
    /// 事件流断开时进度/完成/角标全部收不到，界面必须说一声，否则用户只会觉得
    /// 「下载卡住了」。重连成功后刷新一次当前页，把断线期间错过的状态补上。
    /// </summary>
    private void OnEventStreamStateChanged(object? sender, bool connected)
    {
        AppServices.OnUi(() =>
        {
            if (connected)
            {
                if (_sseWasConnected)
                {
                    ShowToast("已重新连接", "后端事件流恢复，正在刷新状态", InfoBarSeverity.Success);
                    _ = ReloadCurrentAsync();
                }
                _sseWasConnected = true;
            }
            else if (_sseWasConnected)
            {
                ShowToast("后端连接中断", "正在自动重连，期间进度可能不更新", InfoBarSeverity.Warning);
            }
        });
    }

    private void OnBridgeEvent(object? sender, BridgeEvent ev)
    {
        AppServices.OnUi(() =>
        {
            _tasks?.HandleEvent(ev);
            _launch?.HandleEvent(ev);
            _ai?.HandleEvent(ev);
            HandleDock(ev);
            if (ev.Event == "task_count_changed")
            {
                if (ev.Count <= 0)
                {
                    TaskBadge.Visibility = Visibility.Collapsed;
                    TaskBadge.Value = 0;
                }
                else
                {
                    TaskBadge.Value = ev.Count > 99 ? 99 : ev.Count;
                    TaskBadge.Visibility = Visibility.Visible;
                }
            }
            if (ev.Event == "ui_changed")
                _ = ReloadCurrentAsync();
            if (ev.Event == "game_started")
                _ = ApplyLauncherVisibility(true);
            if (ev.Event == "game_exited")
                _ = ApplyLauncherVisibility(false);
            if (ev.Event == "finished")
            {
                var title = ev.Title;
                if (!string.IsNullOrEmpty(ev.TaskId))
                    title = _tasks?.TitleOf(ev.TaskId) ?? title;
                // 「启动游戏」和「微软登录」不是下载任务，退游戏/登录完成不该弹 toast。
                // 以前只挡了前者，登录一完成就冒一条，PySide6 两个都挡。
                if (!string.IsNullOrEmpty(title) && IsSilentTask(title))
                    return;
                if (ev.Success)
                    ShowToast(string.IsNullOrEmpty(title) ? "完成" : title, ev.Message, InfoBarSeverity.Success);
                else if (ev.Message != "已取消")
                    ShowToast(string.IsNullOrEmpty(title) ? "失败" : title, ev.Message, InfoBarSeverity.Error);
            }
        });
    }

    /// <summary>非下载类任务：不进底部下载条、不列进任务页，也不弹完成 toast。</summary>
    internal static bool IsSilentTask(string? title) =>
        !string.IsNullOrEmpty(title)
        && (title.StartsWith("启动游戏", StringComparison.Ordinal)
            || title.StartsWith("微软登录", StringComparison.Ordinal));

    private const int DockLogMaxLines = 2500;

    /// <summary>
    /// 往下载条日志里追加一行。以前是裸 <c>DockLog.Text +=</c>，字符串不可变，
    /// 装大整合包上千行日志就是 O(n²) 拼接，越到后面越卡；而且永不清理。
    /// 这里超过上限就丢掉最老的一批，对齐 PySide6 的 setMaximumBlockCount(2500)。
    /// </summary>
    private void AppendDockLog(string text)
    {
        if (string.IsNullOrEmpty(text)) return;
        _dockLogLines.Add(text);
        if (_dockLogLines.Count > DockLogMaxLines)
            _dockLogLines.RemoveRange(0, _dockLogLines.Count - DockLogMaxLines);
        DockLog.Text = string.Join('\n', _dockLogLines);
    }

    private void ClearDockLog()
    {
        _dockLogLines.Clear();
        DockLog.Text = "";
    }

    private readonly List<string> _dockLogLines = new();

    private void HandleDock(BridgeEvent ev)
    {
        if (ev.Event == "task_added" && !string.IsNullOrEmpty(ev.TaskId))
        {
            if (IsSilentTask(ev.Title)) return;
            _dockActive[ev.TaskId] = ev.Title;
            DockTitle.Text = $"下载任务（{_dockActive.Count}）";
            DockStatus.Text = ev.Title;
            DockProgress.Value = 0;
            DockSpeed.Text = "";
            if (_dockActive.Count == 1) ClearDockLog();
            AppendDockLog($"—— {ev.Title} ——");
            if (ev.Title.Contains("整合包") && !_dockExpanded)
            {
                _dockExpanded = true;
                DockLog.Visibility = Visibility.Visible;
                DockChevron.Glyph = "\uE70E";
            }
            PlaceDock();
        }
        else if (ev.Event == "progress" && _dockActive.ContainsKey(ev.TaskId))
        {
            if (ev.Total > 0) DockProgress.Value = ev.Current * 100.0 / ev.Total;
            SplitMsg(ev.Message, out var st, out var sp);
            DockStatus.Text = string.IsNullOrEmpty(st) ? _dockActive[ev.TaskId] : st;
            DockSpeed.Text = sp;
            DockTitle.Text = $"下载任务（{_dockActive.Count}）";
        }
        else if (ev.Event == "log" && _dockActive.ContainsKey(ev.TaskId) && !string.IsNullOrEmpty(ev.Text))
        {
            AppendDockLog(ev.Text);
        }
        else if (ev.Event == "finished")
        {
            // 只处理确实进过下载条的任务。以前这个分支对任何任务都执行，
            // 于是退游戏时下载条会闪一下「✔ 全部完成」，日志区还被塞进游戏的退出消息。
            if (!_dockActive.Remove(ev.TaskId)) return;
            if (!string.IsNullOrEmpty(ev.Message)) AppendDockLog(ev.Message);
            if (_dockActive.Count == 0)
            {
                DockTitle.Text = "下载任务";
                DockStatus.Text = ev.Success ? "✔ 全部完成" : (ev.Message ?? "已结束");
                DockSpeed.Text = "";
                if (ev.Success) DockProgress.Value = 100;
                PlaceDock();
            }
            else
            {
                DockTitle.Text = $"下载任务（{_dockActive.Count}）";
                DockStatus.Text = _dockActive.Values.FirstOrDefault() ?? "";
            }
        }
    }

    private async void PlaceDock()
    {
        await _dockLock.WaitAsync();
        try
        {
            var want = _dockActive.Count > 0 && _current != "tasks";
            if (want && DockHost.Visibility != Visibility.Visible)
                await Motion.DockShowAsync(DockHost);
            else if (!want && DockHost.Visibility == Visibility.Visible)
                await Motion.DockHideAsync(DockHost);
        }
        finally
        {
            _dockLock.Release();
        }
    }

    private async void DockToggle_Click(object sender, RoutedEventArgs e)
    {
        _dockExpanded = !_dockExpanded;
        DockChevron.Glyph = _dockExpanded ? "\uE70E" : "\uE70D";
        if (_dockExpanded)
        {
            DockLog.Visibility = Visibility.Visible;
            var t = Motion.Tx(DockLog);
            DockLog.Opacity = 0;
            t.TranslateY = 12;
            await Motion.AnimateAsync(DockLog, 1, 0, 0, 1, 200);
        }
        else
        {
            await Motion.AnimateAsync(DockLog, 0, 0, 10, 1, 140, EasingMode.EaseIn);
            DockLog.Visibility = Visibility.Collapsed;
            DockLog.Opacity = 1;
            Motion.Tx(DockLog).TranslateY = 0;
        }
    }

    private readonly Queue<(string Title, string Message, InfoBarSeverity Sev)> _toastQueue = new();
    private DispatcherTimer? _toastTimer;

    /// <summary>
    /// 全局只有一个 InfoBar 当 toast。以前新消息直接盖掉旧消息（连着来两条就只看得到后一条），
    /// 而且从不自动关闭，用户不点 X 就一直挂在界面上挡内容。
    /// 现在改成排队逐条显示 + 到点自动关闭；错误留久一点，方便看清。
    /// </summary>
    public void ShowToast(string title, string message, InfoBarSeverity sev)
    {
        _toastQueue.Enqueue((title, message, sev));
        if (!ToastBar.IsOpen) DequeueToast();
    }

    private void DequeueToast()
    {
        _toastTimer?.Stop();
        if (_toastQueue.Count == 0)
        {
            ToastBar.IsOpen = false;
            return;
        }
        var (title, message, sev) = _toastQueue.Dequeue();
        ToastBar.Title = title;
        ToastBar.Message = message;
        ToastBar.Severity = sev;
        ToastBar.IsOpen = true;

        _toastTimer ??= new DispatcherTimer();
        _toastTimer.Interval = TimeSpan.FromSeconds(sev == InfoBarSeverity.Error ? 8 : 4);
        _toastTimer.Tick -= ToastTimer_Tick;
        _toastTimer.Tick += ToastTimer_Tick;
        _toastTimer.Start();
    }

    private void ToastTimer_Tick(object? sender, object e)
    {
        _toastTimer?.Stop();
        ToastBar.IsOpen = false;
        if (_toastQueue.Count > 0) DequeueToast();
    }

    private void ToastBar_Closed(InfoBar sender, InfoBarClosedEventArgs args)
    {
        _toastTimer?.Stop();
        if (_toastQueue.Count > 0) DequeueToast();
    }

    public static void SplitMsg(string? message, out string status, out string speed)
    {
        var text = message ?? "";
        if (text.Contains("  |  ", StringComparison.Ordinal))
        {
            var i = text.IndexOf("  |  ", StringComparison.Ordinal);
            status = text[..i].Trim();
            speed = text[(i + 5)..].Trim();
            return;
        }
        status = text;
        speed = "";
    }

    private async Task BootExtras()
    {
        if (AppServices.Client is null) return;
        try
        {
            var s = await AppServices.Client.CallAsync<SettingsDto>("get_settings");
            if (s?.AutoCheckUpdate != true) return;
            var info = await AppServices.Client.CallAsync<Dictionary<string, object>>("check_update") ?? new();
            var has = info.TryGetValue("has_update", out var h) && $"{h}".Equals("True", StringComparison.OrdinalIgnoreCase);
            if (has)
            {
                var msg = info.TryGetValue("message", out var m) ? m?.ToString() ?? "" : "";
                ShowToast("发现更新", string.IsNullOrEmpty(msg) ? "到设置里安装" : msg, InfoBarSeverity.Informational);
            }
        }
        catch { }
    }

    // ------------------------------------------------------------------
    // 拖拽导入（对标 PCL2：文件拖进窗口自动识别安装；与 PySide6 主窗口一致）
    // ------------------------------------------------------------------
    private static readonly string[] ImportExts = { ".mrpack", ".jar", ".litemod", ".zip" };

    private void Root_DragOver(object sender, DragEventArgs e)
    {
        if (e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems))
        {
            e.AcceptedOperation = Windows.ApplicationModel.DataTransfer.DataPackageOperation.Copy;
            try
            {
                e.DragUIOverride.Caption = "拖拽导入";
                e.DragUIOverride.IsCaptionVisible = true;
            }
            catch { }
        }
    }

    private async void Root_Drop(object sender, DragEventArgs e)
    {
        try
        {
            if (AppServices.Client is null) return;
            if (!e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems)) return;
            var items = await e.DataView.GetStorageItemsAsync();
            var paths = items.OfType<Windows.Storage.StorageFile>()
                .Select(f => f.Path)
                .Where(p => !string.IsNullOrEmpty(p)
                            && ImportExts.Contains(System.IO.Path.GetExtension(p).ToLowerInvariant()))
                .ToList();
            if (paths.Count == 0)
            {
                ShowToast("无法识别", "支持整合包(.mrpack/.zip)、模组(.jar)、世界、资源包、光影包、数据包",
                          InfoBarSeverity.Warning);
                return;
            }
            var known = new List<ImportInfo>();
            var skipped = new List<string>();
            foreach (var p in paths)
            {
                var info = await AppServices.Client.CallAsync<ImportInfo>("classify_import", new { path = p });
                if (info is null || info.Kind == "unknown")
                    skipped.Add(System.IO.Path.GetFileName(p));
                else
                {
                    info.Path = p;
                    known.Add(info);
                }
            }
            if (known.Count == 0)
            {
                ShowToast("无法识别", "支持整合包(.mrpack/.zip)、模组(.jar)、世界、资源包、光影包、数据包",
                          InfoBarSeverity.Warning);
                return;
            }
            var lines = string.Join("\n", known.Select(i => $"· {i.Name} → {i.Label}"));
            if (skipped.Count > 0)
                lines += "\n" + string.Join("\n", skipped.Select(n => $"· {n} → 无法识别，跳过"));
            var dlg = new ContentDialog
            {
                Title = "拖拽导入",
                Content = new TextBlock
                {
                    Text = $"检测到 {known.Count} 个可导入文件：\n{lines}\n\n整合包会安装对应游戏版本，其余直接放入当前实例对应目录。确认导入？",
                    TextWrapping = TextWrapping.Wrap,
                },
                PrimaryButtonText = "导入",
                CloseButtonText = "取消",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = Content.XamlRoot,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
            var started = 0;
            foreach (var i in known)
            {
                try
                {
                    await AppServices.Client.StartTaskAsync(
                        "import_local_file", new { path = i.Path, kind = i.Kind });
                    started++;
                }
                catch (Exception ex)
                {
                    ShowToast("导入失败", $"{i.Name}: {ex.Message}", InfoBarSeverity.Error);
                }
            }
            if (started > 0)
                ShowToast("已开始导入", $"共 {started} 个任务，进度见「下载任务」", InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            ShowToast("导入失败", ex.Message, InfoBarSeverity.Error);
        }
    }

    private bool _quitOnExit;

    private async Task ApplyLauncherVisibility(bool started)
    {
        if (AppServices.Client is null) return;
        try
        {
            var s = await AppServices.Client.CallAsync<SettingsDto>("get_settings");
            var vis = s?.LauncherVisibility ?? "keep";
            var app = AppWindow.GetFromWindowId(Win32Interop.GetWindowIdFromWindow(AppServices.WindowHandle));
            if (started)
            {
                if (vis == "close")
                {
                    _quitOnExit = true;
                    app.Hide();
                }
                else if (vis is "hide" or "hide_reopen") app.Hide();
                else if (vis == "minimize" && app.Presenter is OverlappedPresenter p)
                    p.Minimize();
            }
            else if (_quitOnExit)
            {
                _quitOnExit = false;
                Close();
            }
            else if (vis == "hide_reopen")
            {
                app.Show();
                Activate();
            }
        }
        catch { }
    }
}
