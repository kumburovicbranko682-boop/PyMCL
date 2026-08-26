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

    public FeedbackPage()
    {
        InitializeComponent();
        CategoryBox.ItemsSource = Categories.Select(c => c.Label).ToList();
        CategoryBox.SelectedIndex = 0;
        _ = LoadFaqAsync();
    }

    public Task ReloadAsync()
    {
        _ = LoadFaqAsync();
        return Task.CompletedTask;
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

    /// <summary>
    /// 对齐 Qt 的 prompt_feedback_consent：后端 has_consent() 不为 true 时提交
    /// 必然抛「需要先同意上传诊断数据」，而本页以前既不询问也没有任何开关，
    /// 反馈功能在 WinUI 下等于摆设。这里现场询问并把选择写回 config。
    /// </summary>
    private async Task<bool> EnsureConsentAsync()
    {
        try
        {
            var settings = await AppServices.Client!.CallAsync<SettingsDto>("get_settings");
            if (settings?.FeedbackConsent == true) return true;
        }
        catch
        {
            // 读取失败就按未同意处理，走下面的询问流程。
        }
        var ok = await Dialogs.ConfirmAsync(XamlRoot, "是否上传诊断数据",
            "同意后才会向开发者上传：\n· 你提交的反馈内容\n· 本机配置（CPU / 内存 / 显卡 / Java / 实例）\n\n暂不同意则不会上传，下次提交时会再次询问。",
            "同意");
        try
        {
            await AppServices.Client!.CallAsync("save_settings", new { data = new { feedback_consent = ok } });
        }
        catch (Exception ex)
        {
            if (ok)
            {
                // 同意没写进 config 的话后端仍会拒绝上传，明确报错而不是假装成功。
                AppServices.Toast?.Invoke("无法保存选择", ex.Message, InfoBarSeverity.Error);
                return false;
            }
        }
        if (!ok)
            AppServices.Toast?.Invoke("未同意", "不同意上传则不会发送反馈", InfoBarSeverity.Warning);
        return ok;
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
        if (!await EnsureConsentAsync()) return;
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
