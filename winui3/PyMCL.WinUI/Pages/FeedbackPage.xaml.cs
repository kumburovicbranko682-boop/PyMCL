using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using PyMCL.Models;
using PyMCL.Services;

namespace PyMCL.Pages;

public sealed partial class FeedbackPage : UserControl
{
    private static readonly (string Key, string Label)[] Categories =
    {
        ("bug", "功能异常"),
        ("crash", "崩溃闪退"),
        ("download", "下载问题"),
        ("multiplayer", "联机"),
        ("ai", "AI 助手"),
        ("ui", "界面体验"),
        ("suggest", "建议"),
        ("other", "其他"),
    };

    // 程序回填勾选状态时不要触发 save_settings（Checked/Unchecked 对代码赋值同样会响）。
    private bool _syncingConsent;

    public FeedbackPage()
    {
        InitializeComponent();
        CategoryBox.ItemsSource = Categories.Select(c => c.Label).ToList();
        CategoryBox.SelectedIndex = 0;
        _ = LoadFaqAsync();
        _ = LoadConsentAsync();
    }

    public Task ReloadAsync()
    {
        _ = LoadFaqAsync();
        _ = LoadConsentAsync();
        return Task.CompletedTask;
    }

    private async Task LoadConsentAsync()
    {
        if (AppServices.Client is null || ConsentBox is null) return;
        try
        {
            var s = await AppServices.Client.CallAsync<SettingsDto>("get_settings");
            _syncingConsent = true;
            ConsentBox.IsChecked = s?.FeedbackConsent == true;
        }
        catch
        {
            // 读不到就保持未勾选；发送时后端仍会给出明确错误。
        }
        finally
        {
            _syncingConsent = false;
        }
    }

    private async void Consent_Toggled(object sender, RoutedEventArgs e)
    {
        if (_syncingConsent || AppServices.Client is null) return;
        var granted = ConsentBox.IsChecked == true;
        try
        {
            await AppServices.Client.CallAsync("save_settings", new { data = new { feedback_consent = granted } });
        }
        catch (Exception ex)
        {
            _syncingConsent = true;
            ConsentBox.IsChecked = !granted;
            _syncingConsent = false;
            AppServices.Toast?.Invoke("保存失败", ex.Message, InfoBarSeverity.Error);
        }
    }

    private async Task LoadFaqAsync()
    {
        if (AppServices.Client is null || FaqHost is null) return;
        FaqHost.Children.Clear();
        try
        {
            var rows = await AppServices.Client.CallAsync<List<HelpArticle>>("help_articles") ?? new();
            if (rows.Count == 0)
            {
                FaqHost.Children.Add(new TextBlock { Text = "暂无帮助条目", Opacity = 0.7 });
                return;
            }
            foreach (var row in rows.Take(12))
            {
                var exp = new Expander
                {
                    Header = string.IsNullOrWhiteSpace(row.Title) ? row.Id : row.Title,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    HorizontalContentAlignment = HorizontalAlignment.Stretch,
                };
                exp.Content = new TextBlock
                {
                    Text = row.Body ?? "",
                    TextWrapping = TextWrapping.Wrap,
                    IsTextSelectionEnabled = true,
                    Margin = new Thickness(4, 0, 4, 8),
                };
                FaqHost.Children.Add(exp);
            }
        }
        catch (Exception ex)
        {
            FaqHost.Children.Add(new TextBlock { Text = "加载帮助失败：" + ex.Message, Opacity = 0.7, TextWrapping = TextWrapping.Wrap });
        }
    }

    private async void Send_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        var title = TitleBox.Text.Trim();
        var body = BodyBox.Text.Trim();
        if (string.IsNullOrWhiteSpace(title) || string.IsNullOrWhiteSpace(body))
        {
            AppServices.Toast?.Invoke("内容不完整", "请填写标题和详细描述", InfoBarSeverity.Warning);
            return;
        }
        if (ConsentBox.IsChecked != true)
        {
            AppServices.Toast?.Invoke("未同意上传", "请先勾选「允许上传诊断数据」，否则后端会拒绝这次反馈", InfoBarSeverity.Warning);
            return;
        }
        var idx = Math.Max(0, CategoryBox.SelectedIndex);
        var category = Categories[idx].Key;
        try
        {
            await AppServices.Client.CallAsync("submit_feedback", new
            {
                category,
                title,
                body,
                contact = ContactBox.Text.Trim(),
                include_sysinfo = AttachBox.IsChecked == true,
            });
            TitleBox.Text = "";
            BodyBox.Text = "";
            AppServices.Toast?.Invoke("已发送", "感谢反馈", InfoBarSeverity.Success);
        }
        catch (Exception ex)
        {
            AppServices.Toast?.Invoke("发送失败", ex.Message, InfoBarSeverity.Error);
        }
    }
}
