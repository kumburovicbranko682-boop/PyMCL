using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using PyMCL.Models;
using PyMCL.Services;
using Windows.Storage.Pickers;
using Windows.UI;
using WinRT.Interop;

namespace PyMCL.Pages;

public sealed partial class CatalogPage : UserControl
{
    private readonly CatalogKind _kind;
    private int _token;

    public CatalogPage() : this(CatalogKind.Mod) { }

    public CatalogPage(CatalogKind kind)
    {
        _kind = kind;
        InitializeComponent();
        SearchTitle.Text = kind.SearchTitle;
        LinkBtn.Content = kind.LinkLabel;
        LocalBtn.Content = kind.LocalLabel;
        SourceBox.SelectedIndex = 0;
        VersionBox.SelectedIndex = 0;
        foreach (var t in kind.Types) TypeBox.Items.Add(t);
        TypeBox.SelectedIndex = 0;
        ShowIdle();
        Loaded += (_, _) => ReloadInstances();
    }

    public async void ReloadInstances()
    {
        if (AppServices.Client is null) return;
        try
        {
            var insts = await AppServices.Client.CallAsync<List<InstanceInfo>>("get_instances") ?? new();
            var cur = InstanceBox.SelectedItem as string;
            InstanceBox.Items.Clear();
            foreach (var i in insts) InstanceBox.Items.Add(i.Name);
            if (cur != null && insts.Any(x => x.Name == cur)) InstanceBox.SelectedItem = cur;
            else if (InstanceBox.Items.Count > 0) InstanceBox.SelectedIndex = 0;
        }
        catch { }
    }

    private void ShowIdle()
    {
        _token++;
        ResultList.Children.Clear();
        ResultList.Children.Add(new TextBlock
        {
            Text = "输入名称后点击搜索",
            Foreground = ThemeBrushes.Mute,
            HorizontalAlignment = HorizontalAlignment.Center,
            Margin = new Thickness(0, 40, 0, 0),
        });
    }

    private void Reset_Click(object sender, RoutedEventArgs e)
    {
        NameEdit.Text = "";
        SourceBox.SelectedIndex = 0;
        VersionBox.SelectedIndex = 0;
        TypeBox.SelectedIndex = 0;
        ShowIdle();
    }

    private void Name_Key(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key == Windows.System.VirtualKey.Enter) _ = SearchAsync();
    }

    private void Search_Click(object sender, RoutedEventArgs e) => _ = SearchAsync();

    private async Task SearchAsync()
    {
        if (AppServices.Client is null) return;
        var token = ++_token;
        ResultList.Children.Clear();
        ResultList.Children.Add(new TextBlock { Text = "正在搜索…", Foreground = ThemeBrushes.Mute, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 40, 0, 0) });
        var query = NameEdit.Text?.Trim() ?? "";
        var source = SourceBox.SelectedItem as string ?? "全部";
        if (!string.IsNullOrEmpty(_kind.DefaultSource)) source = _kind.DefaultSource;
        var typeF = TypeBox.SelectedItem as string ?? "全部";
        var gv = VersionBox.SelectedItem as string ?? VersionBox.Text ?? "";
        if (gv.StartsWith("全部")) gv = "";
        try
        {
            var extra = new Dictionary<string, object?> { ["game_version"] = gv, ["category"] = typeF };
            var rows = await AppServices.Client.CallAsync<List<CatalogItem>>(_kind.SearchMethod, new { query, source, extra }) ?? new();
            if (token != _token) return;
            ResultList.Children.Clear();
            if (rows.Count == 0)
            {
                ResultList.Children.Add(new TextBlock { Text = _kind.EmptySearch, Foreground = ThemeBrushes.Mute, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 40, 0, 0) });
                return;
            }
            var i = 0;
            foreach (var item in rows)
            {
                var row = BuildRow(item);
                Motion.CardEnter(row, i < 8 ? i * 24 : 0, 1.02, i < 8);
                ResultList.Children.Add(row);
                i++;
            }
        }
        catch (Exception ex)
        {
            if (token != _token) return;
            ResultList.Children.Clear();
            ResultList.Children.Add(new TextBlock { Text = "搜索失败: " + ex.Message, Foreground = ThemeBrushes.Mute, Margin = new Thickness(12) });
        }
    }

    private Border BuildRow(CatalogItem item)
    {
        var row = new Border { MinHeight = 72, BorderBrush = ThemeBrushes.Divider, BorderThickness = new Thickness(0, 0, 0, 1), Padding = new Thickness(12, 10, 12, 10) };
        var g = new Grid();
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var tile = new Border
        {
            Width = 52, Height = 52, CornerRadius = new CornerRadius(10),
            Background = ThemeBrushes.Accent,
            Child = new TextBlock { Text = (item.Name.Length > 0 ? item.Name[..1] : "?").ToUpperInvariant(), Foreground = new SolidColorBrush(Microsoft.UI.Colors.White), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, FontWeight = Microsoft.UI.Text.FontWeights.Bold },
        };
        g.Children.Add(tile);
        var info = new StackPanel { Margin = new Thickness(12, 0, 12, 0) };
        info.Children.Add(new TextBlock { Text = item.Name, Foreground = ThemeBrushes.AccentText, FontSize = 14, FontWeight = Microsoft.UI.Text.FontWeights.Bold, TextWrapping = TextWrapping.Wrap });
        if (!string.IsNullOrWhiteSpace(item.Description))
            info.Children.Add(new TextBlock { Text = item.Description, Foreground = ThemeBrushes.Mute, FontSize = 12, TextWrapping = TextWrapping.Wrap, MaxLines = 2 });
        info.Children.Add(new TextBlock
        {
            Text = $"{FmtDownloads(item.Downloads)}  ·  {SrcLabel(item.Source)}",
            Foreground = ThemeBrushes.Mute,
            FontSize = 11,
            Margin = new Thickness(0, 4, 0, 0),
        });
        Grid.SetColumn(info, 1);
        g.Children.Add(info);
        var btn = new Button { Content = "选择版本", Width = 88, Height = 30, Style = (Style)Application.Current.Resources["AccentButtonStyle"] };
        btn.Click += (_, _) => _ = Install(item, tile);
        Grid.SetColumn(btn, 2);
        g.Children.Add(btn);
        row.Child = g;
        return row;
    }

    private static string SrcLabel(string src)
    {
        var s = (src ?? "").ToLowerInvariant();
        if (s.StartsWith("curse")) return "CurseForge";
        if (s.StartsWith("modrinth") || s == "modrinth") return "Modrinth";
        return string.IsNullOrEmpty(src) ? "—" : src;
    }

    private static string FmtDownloads(long n)
    {
        if (n >= 100_000_000) return $"{n / 100_000_000.0:0.#}亿".Replace(".0", "");
        if (n >= 10_000) return $"{n / 10_000.0:0}万";
        return n == 0 ? "—" : n.ToString();
    }

    private async Task Install(CatalogItem item, FrameworkElement? source = null)
    {
        if (AppServices.Client is null) return;
        var instance = InstanceBox.SelectedItem as string ?? "default";
        var gv = VersionBox.SelectedItem as string ?? VersionBox.Text ?? "";
        if (gv.StartsWith("全部")) gv = "";
        var src = string.IsNullOrWhiteSpace(item.Source) || item.Source == "全部"
            ? (SourceBox.SelectedItem as string ?? "Modrinth")
            : item.Source;
        if (!string.IsNullOrEmpty(_kind.DefaultSource)) src = _kind.DefaultSource;
        if (src == "全部") src = "Modrinth";
        var extra = new Dictionary<string, object?>
        {
            ["name"] = item.Name,
            ["source"] = src,
            ["slug"] = item.Slug,
            ["id"] = item.Id,
            ["instance"] = instance,
            ["game_version"] = gv,
        };
        if (!string.IsNullOrWhiteSpace(item.Slug) || item.Id != null)
        {
            var pick = await PickCatalogFile(item, gv, src);
            if (pick is null) return;
            foreach (var kv in pick) extra[kv.Key] = kv.Value;
        }
        AppServices.FlyToTasks?.Invoke(source, item.Name, null);
        try
        {
            if (_kind.IsModpack)
                await AppServices.Client.StartTaskAsync(_kind.InstallMethod, new { name = item.Name, source = src, extra });
            else
                await AppServices.Client.StartTaskAsync(_kind.InstallMethod, new { name = item.Name, instance, extra });
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("安装失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task<Dictionary<string, object?>?> PickCatalogFile(CatalogItem item, string gv, string src)
    {
        List<CatalogFile> files;
        try
        {
            files = await AppServices.Client!.CallAsync<List<CatalogFile>>("list_catalog_files", new
            {
                extra = new Dictionary<string, object?>
                {
                    ["kind"] = _kind.FileKind,
                    ["source"] = src,
                    ["slug"] = item.Slug,
                    ["id"] = item.Id,
                    ["name"] = item.Name,
                    ["game_version"] = gv,
                },
            }) ?? new();
        }
        catch (Exception ex)
        {
            AppServices.Toast?.Invoke("加载版本失败", ex.Message, InfoBarSeverity.Error);
            return new Dictionary<string, object?>();
        }
        var list = new ListView { MaxHeight = 360, SelectionMode = ListViewSelectionMode.Single };
        foreach (var f in files)
        {
            var label = $"{f.VersionNumber}  ·  {string.Join(", ", (f.GameVersions ?? new()).Take(3))}  ·  {string.Join(", ", f.Loaders ?? new())}  ·  {f.Date}  ·  {FmtDownloads(f.Downloads)}\n{f.Filename}";
            list.Items.Add(new ListViewItem { Content = label, Tag = f });
        }
        if (list.Items.Count > 0) list.SelectedIndex = 0;
        var dlg = new ContentDialog
        {
            Title = item.Name,
            Content = list.Items.Count == 0 ? new TextBlock { Text = "没有可安装文件，将尝试安装最新。" } : list,
            PrimaryButtonText = "安装所选",
            SecondaryButtonText = "安装最新",
            CloseButtonText = "取消",
            XamlRoot = XamlRoot,
        };
        var result = await dlg.ShowAsync();
        if (result == ContentDialogResult.None) return null;
        if (result == ContentDialogResult.Secondary) return new Dictionary<string, object?>();
        if (list.SelectedItem is ListViewItem li && li.Tag is CatalogFile cf)
        {
            var ids = new Dictionary<string, object?> { ["source"] = string.IsNullOrEmpty(cf.Source) ? src : cf.Source };
            var sid = (ids["source"] as string ?? "").ToLowerInvariant();
            if (sid.StartsWith("curse"))
            {
                ids["file_id"] = cf.Id;
                ids["version_id"] = cf.Id;
            }
            else ids["version_id"] = cf.Id;
            ids["filename"] = cf.Filename;
            return ids;
        }
        return new Dictionary<string, object?>();
    }

    private async void Link_Click(object sender, RoutedEventArgs e)
    {
        var box = new TextBox { PlaceholderText = _kind.LinkHint };
        var dlg = new ContentDialog { Title = _kind.LinkTitle, Content = box, PrimaryButtonText = "确定", CloseButtonText = "取消", XamlRoot = XamlRoot };
        if (await dlg.ShowAsync() != ContentDialogResult.Primary) return;
        var url = box.Text?.Trim();
        if (string.IsNullOrEmpty(url) || AppServices.Client is null) return;
        var instance = InstanceBox.SelectedItem as string ?? "default";
        var extra = new Dictionary<string, object?> { ["name"] = url, ["url"] = url, ["instance"] = instance, ["source"] = "本地" };
        try
        {
            if (_kind.IsModpack)
                await AppServices.Client.StartTaskAsync(_kind.InstallMethod, new { name = url, source = "本地", extra });
            else
                await AppServices.Client.StartTaskAsync(_kind.InstallMethod, new { name = url, instance, extra });
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("安装失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void Local_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var picker = new FileOpenPicker();
            InitializeWithWindow.Initialize(picker, AppServices.WindowHandle);
            picker.FileTypeFilter.Clear();
            foreach (var ext in _kind.LocalFilter.Split(';', StringSplitOptions.RemoveEmptyEntries))
                picker.FileTypeFilter.Add(ext.StartsWith('.') ? ext : "." + ext);
            var files = await picker.PickMultipleFilesAsync();
            if (files is null || AppServices.Client is null) return;
            foreach (var f in files)
            {
                var extra = new Dictionary<string, object?>
                {
                    ["name"] = f.Path, ["path"] = f.Path, ["instance"] = InstanceBox.SelectedItem as string ?? "default", ["source"] = "本地",
                };
                if (_kind.IsModpack)
                    await AppServices.Client.StartTaskAsync(_kind.InstallMethod, new { name = f.Path, source = "本地", extra });
                else
                    await AppServices.Client.StartTaskAsync(_kind.InstallMethod, new { name = f.Path, instance = extra["instance"], extra });
            }
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("导入失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void Installed_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        ResultList.Children.Clear();
        var inst = InstanceBox.SelectedItem as string ?? "default";
        try
        {
            if (_kind.Title == "Mod")
            {
                var rows = await AppServices.Client.CallAsync<List<ModEntry>>("get_installed_mod_entries", new { instance = inst }) ?? new();
                if (rows.Count == 0)
                {
                    ResultList.Children.Add(new TextBlock { Text = "还没有安装模组", Margin = new Thickness(12), Opacity = 0.7 });
                    return;
                }
                foreach (var row in rows)
                {
                    var g = new Grid { Padding = new Thickness(12, 8, 12, 8) };
                    g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                    g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                    g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                    // 真实模组名优先；有中文译名显示「中文名 (English)」（HMCL 同款）
                    var display = string.IsNullOrEmpty(row.ModName) ? row.Filename : row.ModName;
                    if (!string.IsNullOrEmpty(row.NameCn) && row.NameCn != display)
                        display = $"{row.NameCn} ({display})";
                    var infoCol = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
                    infoCol.Children.Add(new TextBlock { Text = display });
                    var bits = new List<string>();
                    if (!string.IsNullOrEmpty(row.ModVersion)) bits.Add(row.ModVersion);
                    if (!string.IsNullOrEmpty(row.Loader)) bits.Add(row.Loader);
                    if (!string.IsNullOrEmpty(row.ModName)) bits.Add(row.Filename);
                    if (bits.Count > 0)
                        infoCol.Children.Add(new TextBlock { Text = string.Join("  ·  ", bits), Opacity = 0.6, FontSize = 12 });
                    g.Children.Add(infoCol);
                    var sw = new ToggleSwitch { IsOn = row.Enabled, OnContent = "开", OffContent = "关" };
                    var name = row.Filename;
                    sw.Toggled += async (_, _) =>
                    {
                        try
                        {
                            if (sw.IsOn) await AppServices.Client.CallAsync("enable_mod", new { instance = inst, filename = name });
                            else await AppServices.Client.CallAsync("disable_mod", new { instance = inst, filename = name });
                        }
                        catch { Installed_Click(sender, e); }
                    };
                    var del = new Button { Content = "删除" };
                    del.Click += async (_, _) =>
                    {
                        if (!await Dialogs.ConfirmAsync(XamlRoot, "删除文件",
                                $"确定从实例「{inst}」删除「{name}」吗？文件会直接从磁盘移除。"))
                            return;
                        try { await AppServices.Client.CallAsync("delete_mod", new { instance = inst, filename = name }); Installed_Click(sender, e); }
                        catch (Exception ex) { AppServices.Toast?.Invoke("删除失败", ex.Message, InfoBarSeverity.Error); }
                    };
                    if (!string.IsNullOrEmpty(row.McmodUrl))
                        infoCol.Children.Add(new HyperlinkButton
                        {
                            Content = "mcmod 百科",
                            NavigateUri = new Uri(row.McmodUrl),
                            FontSize = 12,
                            Padding = new Thickness(0),
                        });
                    Grid.SetColumn(sw, 1);
                    Grid.SetColumn(del, 2);
                    g.Children.Add(sw);
                    g.Children.Add(del);
                    ResultList.Children.Add(g);
                }
                return;
            }
            ResultList.Children.Add(new TextBlock { Text = "此分类请在实例文件夹中管理", Margin = new Thickness(12), Opacity = 0.7 });
        }
        catch (Exception ex)
        {
            ResultList.Children.Add(new TextBlock { Text = ex.Message, Margin = new Thickness(12) });
        }
    }
}
