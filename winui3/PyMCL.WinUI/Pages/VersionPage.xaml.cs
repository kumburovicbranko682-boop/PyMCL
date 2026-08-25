using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using PyMCL.Models;
using PyMCL.Services;
using Windows.UI;

namespace PyMCL.Pages;

public sealed partial class VersionPage : UserControl
{
    private List<VersionRow> _all = new();
    private readonly List<(CheckBox Box, string Spec)> _installed = new();
    private bool _fetched;

    public VersionPage()
    {
        InitializeComponent();
        LoaderBox.SelectedIndex = 0;
    }

    private void Page_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (PageRoot is null) return;
        var pad = e.NewSize.Width < 640 ? new Thickness(12, 10, 12, 10) : new Thickness(28, 20, 28, 20);
        if (PageRoot.Padding != pad) PageRoot.Padding = pad;
    }

    public async Task ReloadAsync()
    {
        if (AppServices.Client is null) return;
        await FillInstances();
        _all = await AppServices.Client.CallAsync<List<VersionRow>>("get_version_list") ?? new();
        Refill();
        await ReloadInstalled();
        if (_fetched) return;
        _fetched = true;
        _ = FetchRemote();
    }

    private async Task FetchRemote()
    {
        try
        {
            var rows = await AppServices.Client.CallAsync<List<VersionRow>>("fetch_version_list") ?? new();
            _all = rows;
            Refill();
        }
        catch { }
    }

    private async Task FillInstances()
    {
        var insts = await AppServices.Client.CallAsync<List<InstanceInfo>>("get_instances") ?? new();
        var cur = InstanceBox.SelectedItem as string;
        InstanceBox.SelectionChanged -= Instance_Changed;
        InstanceBox.Items.Clear();
        foreach (var i in insts) InstanceBox.Items.Add(i.Name);
        if (cur != null && insts.Any(x => x.Name == cur)) InstanceBox.SelectedItem = cur;
        else if (InstanceBox.Items.Count > 0) InstanceBox.SelectedIndex = 0;
        InstanceBox.SelectionChanged += Instance_Changed;
    }

    private int _limit = 80;
    private bool _firstPaint = true;

    private void Filter_Changed(object sender, object e)
    {
        _limit = 80;
        Refill();
    }

    private async void Instance_Changed(object sender, SelectionChangedEventArgs e) => await ReloadInstalled();

    private void Refill()
    {
        var text = (SearchBox.Text ?? "").Trim().ToLowerInvariant();
        var vtype = "all";
        if (TypePivot.SelectedItem is RadioButton rb && rb.Tag is string tag)
            vtype = tag;
        var filtered = _all.Where(v =>
            (string.IsNullOrEmpty(text) || v.Version.ToLowerInvariant().Contains(text)) &&
            (vtype == "all"
                || (vtype == "old_alpha" && (v.Type == "old_alpha" || v.Type == "old_beta"))
                || v.Type == vtype)).ToList();
        var rows = filtered.Take(_limit).ToList();
        var cards = new List<UIElement>();
        if (rows.Count == 0)
            cards.Add(new TextBlock { Text = "没有匹配的版本", Foreground = ThemeBrushes.Mute, Margin = new Thickness(8) });
        foreach (var v in rows)
            cards.Add(BuildCard(v));
        var pop = _firstPaint;
        _firstPaint = false;
        for (var i = 0; i < cards.Count; i++)
        {
            if (cards[i] is Border b)
                Motion.CardEnter(b, Math.Min(i, 8) * 24, 1.045, pop && i < 10);
        }
        if (filtered.Count > _limit)
        {
            var more = new Button { Content = $"加载更多（还有 {filtered.Count - _limit}）", HorizontalAlignment = HorizontalAlignment.Stretch, Margin = new Thickness(0, 8, 0, 0) };
            more.Click += (_, _) => { _limit += 80; Refill(); };
            cards.Add(more);
        }
        VersionGrid.ItemsSource = cards;
    }

    private Border BuildCard(VersionRow info)
    {
        var labels = new Dictionary<string, (string, Color)>
        {
            ["release"] = ("正式版", Color.FromArgb(255, 47, 163, 107)),
            ["snapshot"] = ("快照", Color.FromArgb(255, 232, 134, 46)),
            ["old_alpha"] = ("远古", Color.FromArgb(255, 124, 92, 214)),
            ["old_beta"] = ("远古", Color.FromArgb(255, 124, 92, 214)),
        };
        var (lab, col) = labels.TryGetValue(info.Type, out var t) ? t : ("快照", Color.FromArgb(255, 232, 134, 46));
        var card = new Border
        {
            MinWidth = 180, Height = 132, Padding = new Thickness(16, 14, 16, 14),
            Background = (Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8),
            Translation = new System.Numerics.Vector3(0, 0, 16),
            Shadow = new ThemeShadow(),
        };
        var g = new Grid();
        g.RowDefinitions.Add(new RowDefinition());
        g.RowDefinitions.Add(new RowDefinition());
        g.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var top = new Grid();
        top.Children.Add(new TextBlock { Text = info.Version, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        top.Children.Add(Pill(lab, col));
        g.Children.Add(top);
        var date = new TextBlock { Text = "发布于 " + info.Date, Foreground = ThemeBrushes.Mute, FontSize = 12, Margin = new Thickness(0, 6, 0, 0) };
        Grid.SetRow(date, 1);
        g.Children.Add(date);
        var btn = new Button { Content = "安装", Height = 30, HorizontalAlignment = HorizontalAlignment.Right };
        btn.Click += async (_, _) =>
        {
            AppServices.FlyToTasks?.Invoke(card, info.Version, "#2FA36B");
            await OpenInstallWizard(info);
        };
        Grid.SetRow(btn, 3);
        g.Children.Add(btn);
        card.Child = g;
        return card;
    }

    private static Border Pill(string text, Color color)
    {
        return new Border
        {
            HorizontalAlignment = HorizontalAlignment.Right,
            Background = new SolidColorBrush(Color.FromArgb(38, color.R, color.G, color.B)),
            CornerRadius = new CornerRadius(9),
            Padding = new Thickness(10, 2, 10, 2),
            Child = new TextBlock { Text = text, Foreground = new SolidColorBrush(color), FontSize = 12, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold },
        };
    }

    private async Task ReloadInstalled()
    {
        InstalledList.Children.Clear();
        _installed.Clear();
        var instance = InstanceBox.SelectedItem as string ?? "default";
        var vers = await AppServices.Client.CallAsync<List<string>>("get_installed_versions", new { instance }) ?? new();
        foreach (var v in vers)
        {
            var row = new Grid();
            var cb = new CheckBox { Content = v };
            var label = v.Contains("fabric", StringComparison.OrdinalIgnoreCase) ? "Fabric"
                : v.Contains("optifine", StringComparison.OrdinalIgnoreCase) ? "OptiFine"
                : v.Contains("liteloader", StringComparison.OrdinalIgnoreCase) ? "LiteLoader"
                : v.Contains("forge", StringComparison.OrdinalIgnoreCase) && !v.Contains("neoforge", StringComparison.OrdinalIgnoreCase) ? "Forge"
                : v.Contains("quilt", StringComparison.OrdinalIgnoreCase) ? "Quilt"
                : v.Contains("neoforge", StringComparison.OrdinalIgnoreCase) ? "NeoForge" : "原版";
            var col = label == "Fabric" ? Color.FromArgb(255, 124, 92, 214)
                : label is "Forge" or "NeoForge" ? Color.FromArgb(255, 232, 134, 46)
                : label == "OptiFine" ? Color.FromArgb(255, 46, 155, 107)
                : label == "LiteLoader" ? Color.FromArgb(255, 76, 139, 245)
                : label == "Quilt" ? Color.FromArgb(255, 124, 92, 214)
                : Color.FromArgb(255, 76, 139, 245);
            row.Children.Add(cb);
            row.Children.Add(Pill(label, col));
            var setup = new Button { Content = "设置", HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 0, 72, 0), Background = new SolidColorBrush(Color.FromArgb(0, 0, 0, 0)), BorderThickness = new Thickness(0) };
            setup.Click += async (_, _) => await OpenSetup(instance, v);
            row.Children.Add(setup);
            InstalledList.Children.Add(row);
            _installed.Add((cb, $"{instance} / {v}"));
        }
    }

    private async void Uninstall_Click(object sender, RoutedEventArgs e)
    {
        var selected = _installed.Where(x => x.Box.IsChecked == true).Select(x => x.Spec).ToList();
        if (selected.Count == 0)
        {
            AppServices.Toast?.Invoke("未选择", "请先勾选要卸载的版本", InfoBarSeverity.Warning);
            return;
        }
        var dlg = new ContentDialog
        {
            Title = "确认卸载",
            Content = "将卸载 " + selected.Count + " 个版本：\n" + string.Join("\n", selected),
            PrimaryButtonText = "确定",
            CloseButtonText = "取消",
            XamlRoot = XamlRoot,
        };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
        foreach (var spec in selected)
        {
            try { await AppServices.Client.CallAsync("uninstall_version", new { spec }); }
            catch (Exception ex) { AppServices.Toast?.Invoke("卸载失败", ex.Message, InfoBarSeverity.Error); }
        }
        await ReloadInstalled();
    }

    private async void Repair_Click(object sender, RoutedEventArgs e)
    {
        var selected = _installed.Where(x => x.Box.IsChecked == true).Select(x => x.Spec).ToList();
        if (selected.Count == 0)
        {
            AppServices.Toast?.Invoke("未选择", "请先勾选要修复的版本", InfoBarSeverity.Warning);
            return;
        }
        foreach (var spec in selected)
        {
            var inst = spec.Contains(" / ") ? spec.Split(" / ", 2)[0] : (InstanceBox.SelectedItem as string ?? "default");
            var vid = spec.Contains(" / ") ? spec.Split(" / ", 2)[1] : spec;
            try { await AppServices.Client.StartTaskAsync("repair_version", new { instance = inst, version = vid }); }
            catch (Exception ex) { AppServices.Toast?.Invoke("修复失败", ex.Message, InfoBarSeverity.Error); }
        }
    }

    private async Task OpenSetup(string instance, string version)
    {
        if (AppServices.Client is null) return;
        VersionSettingsDto? data = null;
        try { data = await AppServices.Client.CallAsync<VersionSettingsDto>("get_version_settings", new { instance, version }); }
        catch (Exception ex)
        {
            AppServices.Toast?.Invoke("读取失败", ex.Message, InfoBarSeverity.Error);
            return;
        }
        var iso = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
        iso.Items.Add("关闭（共用实例目录）");
        iso.Items.Add("隔离存档");
        iso.Items.Add("隔离 Mod 与配置");
        iso.Items.Add("隔离全部");
        iso.SelectedIndex = data?.Isolation == "all" ? 3 : data?.Isolation == "mods" ? 2 : data?.Isolation == "saves" ? 1 : 0;
        var mem = new TextBox { PlaceholderText = "留空则用启动页", Text = data?.MemoryMb?.ToString() ?? "" };
        var jvm = new TextBox { PlaceholderText = "JVM 参数", Text = data?.JvmArgs ?? "", AcceptsReturn = true };
        var server = new TextBox { PlaceholderText = "直连服务器", Text = data?.Server ?? "" };
        var port = new TextBox { PlaceholderText = "25565", Text = data?.Port ?? "" };
        var pre = new TextBox { PlaceholderText = "启动前命令", Text = data?.PreLaunch ?? "" };
        var post = new TextBox { PlaceholderText = "退出后命令", Text = data?.PostLaunch ?? "" };
        var box = new StackPanel { Spacing = 8, MinWidth = 360 };
        var nide = new TextBox { PlaceholderText = "统一通行证服务器 ID", Text = data?.Nide8Id ?? "" };
        var gc = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
        gc.Items.Add("跟随全局");
        gc.Items.Add("G1（推荐）");
        gc.Items.Add("G1");
        gc.Items.Add("调优 G1");
        gc.Items.Add("ZGC");
        gc.Items.Add("不指定");
        var gcMap = new Dictionary<string, int> { ["auto"] = 1, ["g1"] = 2, ["g1_tuned"] = 3, ["zgc"] = 4, ["none"] = 5 };
        gc.SelectedIndex = data?.Gc != null && gcMap.TryGetValue(data.Gc, out var gi) ? gi : 0;
        var winMode = new ComboBox();
        // 「跟随全局」= 存 ""（与 GC 下拉同一约定）。以前只有 窗口/全屏 两项，
        // 保存必固化 "window"，设置里的全局全屏从此对该版本失效。
        winMode.Items.Add("跟随全局");
        winMode.Items.Add("窗口");
        winMode.Items.Add("全屏");
        winMode.SelectedIndex = data?.WindowMode is "maximize" or "fullscreen" ? 2
            : string.IsNullOrEmpty(data?.WindowMode) ? 0 : 1;
        var wait = new CheckBox { Content = "等待启动前命令结束", IsChecked = data?.PreLaunchWait != false };
        box.Children.Add(new TextBlock { Text = "隔离" });
        box.Children.Add(iso);
        box.Children.Add(new TextBlock { Text = "内存 MB" });
        box.Children.Add(mem);
        box.Children.Add(new TextBlock { Text = "JVM" });
        box.Children.Add(jvm);
        box.Children.Add(new TextBlock { Text = "服务器 / 端口" });
        box.Children.Add(server);
        box.Children.Add(port);
        box.Children.Add(new TextBlock { Text = "启动前 / 退出后" });
        box.Children.Add(pre);
        box.Children.Add(post);
        box.Children.Add(new TextBlock { Text = "统一通行证 / GC / 窗口" });
        box.Children.Add(nide);
        box.Children.Add(gc);
        box.Children.Add(winMode);
        box.Children.Add(wait);
        var dlg = new ContentDialog
        {
            Title = "版本设置 · " + version,
            Content = box,
            PrimaryButtonText = "保存",
            CloseButtonText = "取消",
            XamlRoot = XamlRoot,
        };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
        var isoKey = iso.SelectedIndex == 3 ? "all" : iso.SelectedIndex == 2 ? "mods" : iso.SelectedIndex == 1 ? "saves" : "none";
        var gcKey = gc.SelectedIndex switch { 1 => "auto", 2 => "g1", 3 => "g1_tuned", 4 => "zgc", 5 => "none", _ => "" };
        try
        {
            await AppServices.Client.CallAsync("save_version_settings", new
            {
                instance,
                version,
                data = new
                {
                    isolation = isoKey,
                    memory_mb = int.TryParse(mem.Text, out var mb) ? mb : (int?)null,
                    jvm_args = jvm.Text ?? "",
                    server = server.Text ?? "",
                    port = port.Text ?? "",
                    pre_launch = pre.Text ?? "",
                    post_launch = post.Text ?? "",
                    nide8_id = nide.Text ?? "",
                    gc = gcKey,
                    window_mode = winMode.SelectedIndex == 2 ? "maximize"
                        : winMode.SelectedIndex == 1 ? "window" : "",
                    pre_launch_wait = wait.IsChecked == true,
                },
            });
            AppServices.Toast?.Invoke("已保存", "版本设置已写入", InfoBarSeverity.Success);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("保存失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task OpenInstallWizard(VersionRow info)
    {
        if (AppServices.Client is null) return;
        var primary = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var n in new[] { "无（原版）", "Fabric", "Forge", "Quilt", "NeoForge" })
            primary.Items.Add(n);
        primary.SelectedIndex = 0;
        var loaderVer = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
        loaderVer.Items.Add("最新");
        loaderVer.SelectedIndex = 0;
        var of = new CheckBox { Content = "同时安装 OptiFine（Forge / 原版）" };
        var ll = new CheckBox { Content = "同时安装 LiteLoader（1.7–1.12）" };
        var skip = new CheckBox { Content = "跳过资源文件校验" };
        async void ReloadLoaders()
        {
            var name = primary.SelectedItem as string ?? "无";
            of.IsEnabled = name.StartsWith("无") || name == "Forge";
            if (!of.IsEnabled) of.IsChecked = false;
            var loader = name.StartsWith("无") ? "" : name;
            loaderVer.Items.Clear();
            loaderVer.Items.Add("最新");
            loaderVer.SelectedIndex = 0;
            if (string.IsNullOrEmpty(loader) || AppServices.Client is null) return;
            try
            {
                var rows = await AppServices.Client.CallAsync<List<LoaderVer>>("list_loader_versions", new { mc_version = info.Version, loader }) ?? new();
                foreach (var r in rows.Take(30))
                    loaderVer.Items.Add(string.IsNullOrEmpty(r.Label) ? r.Id : r.Label);
            }
            catch { }
        }
        primary.SelectionChanged += (_, _) => ReloadLoaders();
        var box = new StackPanel { Spacing = 8, MinWidth = 380 };
        box.Children.Add(new TextBlock { Text = "主加载器" });
        box.Children.Add(primary);
        box.Children.Add(new TextBlock { Text = "加载器版本" });
        box.Children.Add(loaderVer);
        box.Children.Add(of);
        box.Children.Add(ll);
        box.Children.Add(skip);
        var dlg = new ContentDialog
        {
            Title = "安装 " + info.Version,
            Content = box,
            PrimaryButtonText = "开始安装",
            CloseButtonText = "取消",
            XamlRoot = XamlRoot,
        };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
        var pname = primary.SelectedItem as string ?? "无（原版）";
        var loader = pname.StartsWith("无") ? "无" : pname;
        var lv = loaderVer.SelectedItem as string ?? "最新";
        if (lv == "最新") lv = "";
        var extra = new Dictionary<string, object?>
        {
            ["optifine"] = of.IsChecked == true,
            ["liteloader"] = ll.IsChecked == true,
            ["skip_assets"] = skip.IsChecked == true,
        };
        if (!string.IsNullOrEmpty(lv)) extra["loader_version"] = lv;
        var inst = InstanceBox.SelectedItem as string ?? "default";
        try
        {
            await AppServices.Client.StartTaskAsync("install_game", new { version = info.Version, loader, loader_version = lv, instance = inst, extra });
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("安装失败", ex.Message, InfoBarSeverity.Error); }
    }
}
