using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using PyMCL.Models;
using PyMCL.Services;
using Windows.UI;

namespace PyMCL.Pages;

public sealed partial class InstancePage : UserControl
{
    public InstancePage()
    {
        InitializeComponent();
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
        var insts = await AppServices.Client.CallAsync<List<InstanceInfo>>("get_instances") ?? new();
        var items = new List<UIElement>();
        foreach (var info in insts)
            items.Add(BuildCard(info));
        items.Add(BuildNewCard());
        for (var i = 0; i < items.Count; i++)
            Motion.CardEnter(items[i], Math.Min(i, 12) * 36);
        GridView.ItemsSource = items;
    }

    private Border BuildCard(InstanceInfo info)
    {
        var card = new Border
        {
            MinWidth = 200, Height = 138,
            Background = (Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8), Padding = new Thickness(16, 14, 16, 14),
            Translation = new System.Numerics.Vector3(0, 0, 16),
            Shadow = new ThemeShadow(),
        };
        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var top = new Grid();
        top.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        top.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var tile = IconTile(info.Name, 40);
        var names = new StackPanel { Margin = new Thickness(10, 0, 0, 0) };
        names.Children.Add(new TextBlock { Text = info.Name, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        names.Children.Add(new TextBlock { Text = $"{info.Versions} 个版本", Foreground = Mute(), FontSize = 12 });
        Grid.SetColumn(names, 1);
        top.Children.Add(tile);
        top.Children.Add(names);
        root.Children.Add(top);
        var mc = new TextBlock { Text = info.Mc, Foreground = Mute(), FontSize = 12, Margin = new Thickness(0, 6, 0, 0) };
        Grid.SetRow(mc, 1);
        root.Children.Add(mc);
        var java = new TextBlock { Text = "Java · " + (string.IsNullOrEmpty(info.JavaLabel) ? "自动选择" : info.JavaLabel), Foreground = Mute(), FontSize = 12 };
        Grid.SetRow(java, 2);
        root.Children.Add(java);

        var actions = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        actions.Children.Add(IconBtn("📁", () => _ = Open(info.Name)));
        actions.Children.Add(IconBtn("☕", () => _ = PickJava(info.Name)));
        actions.Children.Add(IconBtn("✎", () => _ = Rename(info.Name)));
        actions.Children.Add(IconBtn("⧉", () => _ = Duplicate(info.Name)));
        if (!string.IsNullOrEmpty(info.Pack))
            actions.Children.Add(IconBtn("⟳", () => _ = CheckPackUpdate(info.Name)));
        actions.Children.Add(IconBtn("⇪", () => _ = Export(info.Name)));
        actions.Children.Add(IconBtn("🗑", () => _ = Delete(info.Name)));
        Grid.SetRow(actions, 4);
        root.Children.Add(actions);
        card.Child = root;
        return card;
    }

    private Border BuildNewCard()
    {
        var card = new Border
        {
            MinWidth = 200, Height = 138,
            Background = (Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(8),
            Translation = new System.Numerics.Vector3(0, 0, 16),
            Shadow = new ThemeShadow(),
        };
        var sp = new StackPanel { VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HorizontalAlignment.Center };
        sp.Children.Add(new TextBlock { Text = "＋ 新建实例", FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, HorizontalAlignment = HorizontalAlignment.Center });
        sp.Children.Add(new TextBlock { Text = "隔离的版本、模组与存档", Foreground = Mute(), FontSize = 12, HorizontalAlignment = HorizontalAlignment.Center });
        card.Child = sp;
        card.Tapped += async (_, _) => await Create();
        return card;
    }

    private static Button IconBtn(string t, Action a)
    {
        var b = new Button { Content = t, Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent), BorderThickness = new Thickness(0), Width = 32, Height = 28 };
        b.Click += (_, _) => a();
        return b;
    }

    private static Border IconTile(string name, int size)
    {
        var ch = string.IsNullOrEmpty(name) ? "?" : name[..1].ToUpperInvariant();
        return new Border
        {
            Width = size, Height = size, CornerRadius = new CornerRadius(10),
            Background = new SolidColorBrush(Color.FromArgb(255, 46, 155, 107)),
            Child = new TextBlock { Text = ch, Foreground = new SolidColorBrush(Microsoft.UI.Colors.White), FontWeight = Microsoft.UI.Text.FontWeights.Bold, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, FontSize = size * 0.42 },
        };
    }

    private static Brush Mute() => ThemeBrushes.Mute;

    private async Task Create()
    {
        var name = await yrompt("新建实例", "实例名称", "例如：模组生存");
        if (string.IsNullOrWhiteSpace(name) || AppServices.Client is null) return;
        try { await AppServices.Client.CallAsync("create_instance", new { name }); await ReloadAsync(); }
        catch (Exception ex) { AppServices.Toast?.Invoke("创建失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task Delete(string name)
    {
        if (!await Confirm("删除实例", $"确定删除实例「{name}」？其中的存档与配置将一并移除。")) return;
        try { await AppServices.Client.CallAsync("delete_instance", new { name }); await ReloadAsync(); }
        catch (Exception ex) { AppServices.Toast?.Invoke("删除失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task Rename(string name)
    {
        var ne = await yrompt("重命名实例", "新名称", "", name);
        if (string.IsNullOrWhiteSpace(ne) || AppServices.Client is null) return;
        try { await AppServices.Client.CallAsync("rename_instance", new { name, new_name = ne }); await ReloadAsync(); }
        catch (Exception ex) { AppServices.Toast?.Invoke("重命名失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task Open(string name)
    {
        try { await AppServices.Client.CallAsync("open_instance_folder", new { name }); }
        catch (Exception ex) { AppServices.Toast?.Invoke("无法打开", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task Duplicate(string name)
    {
        var ne = await yrompt("复制实例", "新实例名称（版本、模组、存档都会复制）", "", name + "-副本");
        if (string.IsNullOrWhiteSpace(ne) || AppServices.Client is null) return;
        try
        {
            await AppServices.Client.StartTaskAsync("duplicate_instance", new { name, new_name = ne });
            AppServices.Toast?.Invoke("开始复制", $"{name} → {ne}", InfoBarSeverity.Success);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("复制失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task CheckPackUpdate(string name)
    {
        if (AppServices.Client is null) return;
        ModpackUpdateInfo? info;
        try { info = await AppServices.Client.CallAsync<ModpackUpdateInfo>("check_modpack_update", new { instance = name }); }
        catch (Exception ex)
        {
            AppServices.Toast?.Invoke("检查整合包更新失败", ex.Message, InfoBarSeverity.Error);
            return;
        }
        if (info is null || !info.Update)
        {
            AppServices.Toast?.Invoke("整合包更新", $"「{info?.Name ?? name}」已是最新版本（{info?.Current ?? "?"}）", InfoBarSeverity.Informational);
            return;
        }
        var ok = await Confirm(
            "发现整合包新版本",
            $"{info.Name}：{info.Current} → {info.Latest}\n\n更新会重新安装整合包文件并清理旧版本残留的模组；存档、截图与手动添加的模组不受影响。是否更新？");
        if (!ok) return;
        try { await AppServices.Client.StartTaskAsync("update_modpack", new { instance = name }); }
        catch (Exception ex) { AppServices.Toast?.Invoke("更新失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task Export(string name)
    {
        try
        {
            await AppServices.Client.StartTaskAsync("export_modpack", new { instance = name });
            AppServices.Toast?.Invoke("开始导出", name + " → exports/", InfoBarSeverity.Success);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("导出失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task PickJava(string name)
    {
        var opts = await AppServices.Client.CallAsync<List<JavaOption>>("java_combo_options", new { instance = name, scan_system = true }) ?? new();
        var labels = opts.Select(o => o.Label).ToList();
        var current = await AppServices.Client.CallAsync<string>("java_combo_label_for", new { instance = name, options = opts }) ?? "自动选择";
        var combo = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var l in labels) combo.Items.Add(l);
        combo.SelectedItem = labels.Contains(current) ? current : labels.FirstOrDefault();
        var dlg = new ContentDialog
        {
            Title = "选择 Java",
            Content = new StackPanel
            {
                Spacing = 8,
                Children =
                {
                    new TextBlock { Text = $"实例「{name}」启动时使用的 Java。自动选择会按游戏版本匹配。", TextWrapping = TextWrapping.Wrap },
                    combo,
                },
            },
            PrimaryButtonText = "确定",
            CloseButtonText = "取消",
            XamlRoot = XamlRoot,
        };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
        var chosen = combo.SelectedItem as string ?? "自动选择";
        var value = opts.FirstOrDefault(o => o.Label == chosen)?.Value ?? "自动选择";
        try { await AppServices.Client.CallAsync("set_instance_java", new { name, java = value }); await ReloadAsync(); }
        catch (Exception ex) { AppServices.Toast?.Invoke("保存失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task<string?> yrompt(string title, string label, string ph, string text = "")
    {
        var box = new TextBox { PlaceholderText = ph, Text = text };
        var dlg = new ContentDialog
        {
            Title = title,
            Content = new StackPanel { Spacing = 8, Children = { new TextBlock { Text = label }, box } },
            PrimaryButtonText = "确定",
            CloseButtonText = "取消",
            XamlRoot = XamlRoot,
        };
        return await dlg.ShowAsync() == ContentDialogResult.Primary ? box.Text?.Trim() : null;
    }

    private async Task<bool> Confirm(string title, string msg)
    {
        var dlg = new ContentDialog { Title = title, Content = msg, PrimaryButtonText = "确定", CloseButtonText = "取消", XamlRoot = XamlRoot };
        return await dlg.ShowAsync() == ContentDialogResult.Primary;
    }
}
