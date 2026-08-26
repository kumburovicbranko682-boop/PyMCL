using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using PyMCL.Models;
using PyMCL.Services;

namespace PyMCL.Pages;

public sealed partial class SettingsPage : UserControl
{
    public SettingsPage()
    {
        InitializeComponent();
        Loaded += OnFirstLoaded;
    }

    private void Page_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (PageRoot is null) return;
        var pad = e.NewSize.Width < 640 ? new Thickness(12, 10, 12, 10) : new Thickness(28, 20, 28, 20);
        if (PageRoot.Padding != pad) PageRoot.Padding = pad;
    }

    private void OnFirstLoaded(object sender, RoutedEventArgs e)
    {
        Loaded -= OnFirstLoaded;
        Motion.EnableHoverLift(CardStorage, 1.02);
        Motion.EnableHoverLift(CardPerf, 1.02);
        Motion.EnableHoverLift(CardAccount, 1.02);
        Motion.EnableHoverLift(CardAi, 1.02);
        AiModeBox.SelectionChanged += (_, _) => SyncAiMode();
    }

    public async Task ReloadAsync()
    {
        if (AppServices.Client is null) return;
        var s = await AppServices.Client.CallAsync<SettingsDto>("get_settings");
        if (s is null) return;
        ShareLibs.IsOn = s.ShareLibraries;
        ShareAssets.IsOn = s.ShareAssets;
        ThreadsSpin.Value = s.DownloadThreads;
        MemorySpin.Value = s.DefaultMemoryMb;
        if (s.DefaultResolution is { Count: >= 2 })
        {
            WidthSpin.Value = s.DefaultResolution[0];
            HeightSpin.Value = s.DefaultResolution[1];
        }
        MsClient.Text = s.MsClientId;
        CurseKey.Password = s.CurseforgeApiKey;
        AiGateway.Text = s.AiGatewayUrl;
        AiBase.Text = s.AiBaseUrl;
        AiKey.Password = s.AiApiKey;
        AiModel.Text = s.AiModel;
        AiModeBox.SelectedIndex = s.AiMode == "custom" ? 1 : 0;
        SyncAiMode();
        if (IsoBox != null)
        {
            IsoBox.SelectedIndex = s.DefaultIsolation == "all" ? 3 : s.DefaultIsolation == "mods" ? 2 : s.DefaultIsolation == "saves" ? 1 : 0;
        }
        if (JvmEdit != null) JvmEdit.Text = s.DefaultJvmArgs ?? "";
        if (VisBox != null)
            VisBox.SelectedIndex = s.LauncherVisibility switch { "minimize" => 1, "hide" => 2, "hide_reopen" => 3, "close" => 4, _ => 0 };
        if (GcBox != null)
            GcBox.SelectedIndex = s.GcPreset switch { "g1" => 1, "g1_tuned" => 2, "zgc" => 3, "none" => 4, _ => 0 };
        if (SourceBox != null)
            SourceBox.SelectedIndex = s.DownloadSource switch { "official" => 1, "bmclapi" => 2, _ => 0 };
        if (LimitSpin != null) LimitSpin.Value = s.DownloadLimitKbps;
        if (HomeBox != null)
            HomeBox.SelectedIndex = s.HomepageMode == "custom" ? 1 : s.HomepageMode == "blank" ? 2 : 0;
        if (HomePath != null) HomePath.Text = s.CustomHomepage ?? "";
        if (AutoUpd != null) AutoUpd.IsOn = s.AutoCheckUpdate;
        if (MultiSw != null) MultiSw.IsOn = s.AllowMultiInstance;
        if (FlyAnimSw != null) FlyAnimSw.IsOn = s.UiFlyAnimation;
        if (FlyDurSpin != null) FlyDurSpin.Value = s.UiFlyDurationMs > 0 ? s.UiFlyDurationMs : 620;
        RootLabel.Text = "启动器主目录: " + s.Root;
    }

    /// <summary>
    /// 清空 NumberBox 会让 Value 变成 double.NaN，直接 (int) 强转得到 0。
    /// 线程数/内存那两个键后端有 <c>or 8</c> 之类的兜底，但分辨率是裸 <c>int(res[0])</c>——
    /// 用户把宽度框一清再保存，config.json 里就真躺着 width=0，游戏拿到 --width 0。
    /// </summary>
    private static int SpinValue(NumberBox box, int fallback)
    {
        var v = box?.Value ?? double.NaN;
        if (double.IsNaN(v) || double.IsInfinity(v)) return fallback;
        return (int)Math.Round(v);
    }

    private static string TagOf(ComboBox box, string fallback) =>
        (box?.SelectedItem as ComboBoxItem)?.Tag as string ?? fallback;

    /// <summary>AI 相关的键单独打包：测试连接只需要这一组，不该把整页设置一并落盘。</summary>
    private object BuildAiPatch() => new
    {
        ai_mode = TagOf(AiModeBox, "public"),
        ai_gateway_url = AiGateway.Text?.Trim() ?? "",
        ai_base_url = AiBase.Text?.Trim() ?? "",
        ai_api_key = AiKey.Password?.Trim() ?? "",
        ai_model = string.IsNullOrWhiteSpace(AiModel.Text) ? "deepseek-v4-flash" : AiModel.Text.Trim(),
    };

    private async void Save_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try
        {
            await AppServices.Client.CallAsync("save_settings", new
            {
                data = new
                {
                    share_libraries = ShareLibs.IsOn,
                    share_assets = ShareAssets.IsOn,
                    download_threads = SpinValue(ThreadsSpin, 8),
                    default_memory_mb = SpinValue(MemorySpin, 4096),
                    default_resolution = new[] { SpinValue(WidthSpin, 854), SpinValue(HeightSpin, 480) },
                    ms_client_id = MsClient.Text?.Trim() ?? "",
                    curseforge_api_key = CurseKey.Password?.Trim() ?? "",
                    ai_mode = TagOf(AiModeBox, "public"),
                    ai_gateway_url = AiGateway.Text?.Trim() ?? "",
                    ai_base_url = AiBase.Text?.Trim() ?? "",
                    ai_api_key = AiKey.Password?.Trim() ?? "",
                    ai_model = string.IsNullOrWhiteSpace(AiModel.Text) ? "deepseek-v4-flash" : AiModel.Text.Trim(),
                    default_isolation = TagOf(IsoBox, "none"),
                    default_jvm_args = JvmEdit?.Text?.Trim() ?? "",
                    launcher_visibility = TagOf(VisBox, "keep"),
                    gc_preset = TagOf(GcBox, "auto"),
                    download_source = TagOf(SourceBox, "auto"),
                    download_limit_kbps = SpinValue(LimitSpin, 0),
                    homepage_mode = TagOf(HomeBox, "news"),
                    custom_homepage = HomePath?.Text?.Trim() ?? "",
                    auto_check_update = AutoUpd.IsOn,
                    allow_multi_instance = MultiSw?.IsOn ?? false,
                    ui_fly_animation = FlyAnimSw?.IsOn ?? true,
                    ui_fly_duration_ms = SpinValue(FlyDurSpin, 620),
                },
            });
            AppServices.Toast?.Invoke("已保存", "设置已写入 config.json", InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            AppServices.Toast?.Invoke("保存失败", ex.Message, InfoBarSeverity.Error);
        }
    }

    private void SyncAiMode()
    {
        var custom = (AiModeBox.SelectedItem as ComboBoxItem)?.Tag as string == "custom";
        AiPublicPanel.Visibility = custom ? Visibility.Collapsed : Visibility.Visible;
        AiCustomPanel.Visibility = custom ? Visibility.Visible : Visibility.Collapsed;
    }

    private async void TestAi_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try
        {
            // 直接把表单里的 AI 值作为 settings 传给后端试连，与 Qt 设置页一致：
            // 测试不落盘。以前桥上没有 settings 参数，只能先 save_settings 再测，
            // 就算测试失败，没验证过的密钥也已经被永久写进 config.json。
            var msg = await AppServices.Client.CallAsync<string>("test_ai_connection", new { settings = BuildAiPatch() });
            AppServices.Toast?.Invoke("AI 连接成功", msg ?? "已连通", InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            AppServices.Toast?.Invoke("AI 连接失败", ex.Message, InfoBarSeverity.Error);
        }
    }

    private async void Update_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try
        {
            var info = await AppServices.Client.CallAsync<Dictionary<string, object>>("check_update") ?? new();
            var has = info.TryGetValue("has_update", out var h) && h is bool b && b;
            var msg = info.TryGetValue("message", out var m) ? m?.ToString() ?? "" : "";
            if (has)
            {
                await AppServices.Client.StartTaskAsync("start_self_update");
                AppServices.Toast?.Invoke("发现更新", msg, InfoBarSeverity.Success);
            }
            else AppServices.Toast?.Invoke("检查更新", string.IsNullOrEmpty(msg) ? "已是最新" : msg, InfoBarSeverity.Informational);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("检查失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void Clean_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try
        {
            var preview = await AppServices.Client.CallAsync<Dictionary<string, object>>("cleaner_preview") ?? new();
            var n = preview.TryGetValue("count", out var c) ? c?.ToString() : "0";
            var dlg = new ContentDialog
            {
                Title = "清理文件",
                Content = "将删除未引用库 / .part / 更新缓存，共 " + n + " 个",
                PrimaryButtonText = "清理",
                CloseButtonText = "取消",
                XamlRoot = XamlRoot,
            };
            if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
            var result = await AppServices.Client.CallAsync<Dictionary<string, object>>("cleaner_apply") ?? new();
            AppServices.Toast?.Invoke("清理完成", "删除 " + (result.TryGetValue("removed", out var r) ? r : 0) + " 个文件", InfoBarSeverity.Success);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("清理失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void GlobalMods_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try { await AppServices.Client.CallAsync("open_global_mods"); }
        catch (Exception ex) { AppServices.Toast?.Invoke("打开失败", ex.Message, InfoBarSeverity.Error); }
    }
}
