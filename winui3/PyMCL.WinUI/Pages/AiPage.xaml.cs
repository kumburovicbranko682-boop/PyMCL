using System.Text.Json;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using PyMCL.Models;
using PyMCL.Services;

namespace PyMCL.Pages;

public sealed partial class AiPage : UserControl
{
    private string _activeId = "";
    private string _stream = "";
    private TextBlock? _assistant;
    private string _lastUser = "";
    private bool _busy;
    private bool _picking;
    private DispatcherQueueTimer? _flushTimer;

    public AiPage()
    {
        InitializeComponent();
        _flushTimer = DispatcherQueue.CreateTimer();
        _flushTimer.IsRepeating = false;
        _flushTimer.Interval = TimeSpan.FromMilliseconds(33);
        _flushTimer.Tick += (_, _) =>
        {
            SetAssistant(string.IsNullOrEmpty(_stream) ? "…" : _stream);
            ScrollBottom();
        };
    }

    public async Task ReloadAsync()
    {
        if (AppServices.Client is null) return;
        try
        {
            var s = await AppServices.Client.CallAsync<SettingsDto>("get_settings");
            var model = string.IsNullOrWhiteSpace(s?.AiModel) ? "deepseek-v4-flash" : s!.AiModel;
            StatusLabel.Text = (s?.AiMode == "custom" ? "自定义 · " : "公益接口 · ") + model;
        }
        catch { }
        await LoadStoreAsync();
    }

    public void HandleEvent(BridgeEvent ev)
    {
        if (ev.Event == "ai.delta")
        {
            _stream += ev.Text ?? "";
            if (_flushTimer is { IsRunning: false })
                _flushTimer.Start();
        }
        else if (ev.Event == "ai.status")
        {
            var kind = ev.Kind;
            if (kind == "tool" || kind == "tool_run" || kind == "tool_done" || kind == "tool_skip")
            {
                var prefix = kind switch
                {
                    "tool_run" => "执行中：",
                    "tool_done" => "完成：",
                    "tool_skip" => "已跳过：",
                    _ => "准备：",
                };
                AddCaption(prefix + (string.IsNullOrWhiteSpace(ev.Label) ? ev.Name : ev.Label));
            }
        }
        else if (ev.Event == "ai.confirm")
            _ = ConfirmAsync(ev.Label, ev.Name);
        else if (ev.Event == "ai.ask")
            _ = AskAsync(ev.PayloadJson);
        else if (ev.Event == "ai.done")
        {
            StopFlush();
            SetAssistant(string.IsNullOrWhiteSpace(ev.Text) ? _stream : ev.Text);
            ScrollBottom();
            SetBusy(false);
        }
        else if (ev.Event == "ai.fail")
        {
            StopFlush();
            SetAssistant(string.IsNullOrWhiteSpace(ev.Text) ? "出错了" : ev.Text);
            SetBusy(false);
            AppServices.Toast?.Invoke(ev.Stopped ? "已停止" : "助手出错", ev.Text, ev.Stopped ? InfoBarSeverity.Informational : InfoBarSeverity.Error);
        }
    }

    private async Task LoadStoreAsync()
    {
        if (AppServices.Client is null) return;
        var store = await AppServices.Client.CallAsync<AiStoreDto>("ai_list_chats");
        FillList(store);
    }

    private void FillList(AiStoreDto? store)
    {
        _picking = true;
        ChatList.ItemsSource = store?.Chats ?? new();
        _activeId = store?.ActiveId ?? "";
        AiChatDto? cur = null;
        foreach (var c in store?.Chats ?? new())
        {
            if (c.Id == _activeId) { cur = c; break; }
        }
        ChatList.SelectedItem = cur;
        RenderMessages(cur);
        _picking = false;
    }

    private void RenderMessages(AiChatDto? chat)
    {
        MsgHost.Children.Clear();
        _assistant = null;
        _stream = "";
        var msgs = chat?.Messages ?? new();
        if (msgs.Count == 0)
        {
            AddBubble("助手", "我是启动器助手。可以帮你下游戏、装模组和整合包、看启动报错、查模组冲突、改常用配置。", false);
            return;
        }
        foreach (var m in msgs)
            AddBubble(m.Role == "user" ? "我" : "助手", m.Content, m.Role == "user");
        ScrollBottom();
    }

    private void AddBubble(string who, string text, bool mine)
    {
        var card = new Border
        {
            CornerRadius = new CornerRadius(10),
            Padding = new Thickness(12, 8, 12, 8),
            MaxWidth = 640,
            HorizontalAlignment = mine ? HorizontalAlignment.Right : HorizontalAlignment.Left,
            Background = mine
                ? new Microsoft.UI.Xaml.Media.SolidColorBrush(Windows.UI.Color.FromArgb(255, 232, 246, 239))
                : (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
        };
        var stack = new StackPanel { Spacing = 2 };
        stack.Children.Add(new TextBlock { Text = who, FontSize = 11, Opacity = 0.6 });
        var body = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        stack.Children.Add(body);
        card.Child = stack;
        MsgHost.Children.Add(card);
        if (!mine) _assistant = body;
    }

    private void AddCaption(string text)
    {
        MsgHost.Children.Add(new TextBlock
        {
            Text = text,
            FontSize = 12,
            Opacity = 0.75,
            TextWrapping = TextWrapping.Wrap,
        });
        ScrollBottom();
    }

    private void SetAssistant(string text)
    {
        if (_assistant is null)
            AddBubble("助手", text, false);
        else
            _assistant.Text = text;
    }

    private void StopFlush()
    {
        if (_flushTimer is { IsRunning: true })
            _flushTimer.Stop();
    }

    private void ScrollBottom()
    {
        MsgScroll.ChangeView(null, MsgScroll.ScrollableHeight, null, true);
    }

    private void SetBusy(bool on)
    {
        _busy = on;
        StopBtn.IsEnabled = on;
        RetryBtn.IsEnabled = !on;
    }

    private async void Send_Click(object sender, RoutedEventArgs e) => await SendAsync(InputBox.Text);

    private async void Chip_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button b && b.Tag is string t)
            await SendAsync(t);
    }

    private async Task SendAsync(string? raw)
    {
        var text = (raw ?? "").Trim();
        if (string.IsNullOrEmpty(text) || AppServices.Client is null || _busy) return;
        InputBox.Text = "";
        _lastUser = text;
        AddBubble("我", text, true);
        _stream = "";
        _assistant = null;
        AddBubble("助手", "正在想…", false);
        SetBusy(true);
        try
        {
            await AppServices.Client.CallAsync("ai_send", new { text, chat_id = _activeId });
        }
        catch (Exception ex)
        {
            SetAssistant(ex.Message);
            SetBusy(false);
        }
    }

    private async void Stop_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try { await AppServices.Client.CallAsync("ai_stop"); } catch { }
    }

    private async void Retry_Click(object sender, RoutedEventArgs e)
    {
        if (!string.IsNullOrWhiteSpace(_lastUser))
            await SendAsync(_lastUser);
    }

    private async void NewChat_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try
        {
            var data = await AppServices.Client.CallAsync<AiStoreDto>("ai_new_chat");
            FillList(data);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("新建对话失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void DeleteChat_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null || string.IsNullOrEmpty(_activeId)) return;
        if (!await Dialogs.ConfirmAsync(XamlRoot, "删除会话", "确定删除当前 AI 会话吗？该会话的全部对话记录都会丢失。"))
            return;
        try
        {
            var data = await AppServices.Client.CallAsync<AiStoreDto>("ai_delete_chat", new { chat_id = _activeId });
            FillList(data);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("删除失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void ChatList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_picking || ChatList.SelectedItem is not AiChatDto chat || AppServices.Client is null) return;
        if (chat.Id == _activeId) return;
        try
        {
            var data = await AppServices.Client.CallAsync<AiStoreDto>("ai_set_active", new { chat_id = chat.Id });
            FillList(data);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("切换对话失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async Task ConfirmAsync(string? label, string? name)
    {
        if (AppServices.Client is null) return;
        var dlg = new ContentDialog
        {
            Title = "需要你点一下确认",
            Content = label ?? name ?? "执行写操作",
            PrimaryButtonText = "确认执行",
            CloseButtonText = "取消",
            XamlRoot = XamlRoot,
        };
        var r = await dlg.ShowAsync();
        await AppServices.Client.CallAsync("ai_confirm", new { ok = r == ContentDialogResult.Primary });
    }

    private async Task AskAsync(string? json)
    {
        if (AppServices.Client is null) return;
        var prompt = "请选择";
        try
        {
            using var doc = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "{}" : json);
            if (doc.RootElement.TryGetProperty("title", out var t) && t.GetString() is string ts && ts.Length > 0)
                prompt = ts;
            else if (doc.RootElement.TryGetProperty("questions", out var qs) && qs.ValueKind == JsonValueKind.Array && qs.GetArrayLength() > 0)
            {
                var q0 = qs[0];
                if (q0.TryGetProperty("prompt", out var p))
                    prompt = p.GetString() ?? prompt;
            }
        }
        catch { }
        var dlg = new ContentDialog
        {
            Title = prompt,
            Content = new TextBox { PlaceholderText = "可填其他，或直接确定" },
            PrimaryButtonText = "确定",
            CloseButtonText = "跳过",
            XamlRoot = XamlRoot,
        };
        var r = await dlg.ShowAsync();
        object? payload = null;
        if (r == ContentDialogResult.Primary)
        {
            var extra = (dlg.Content as TextBox)?.Text?.Trim() ?? "";
            payload = new
            {
                answers = new Dictionary<string, object>
                {
                    ["q1"] = new { ids = new[] { "other" }, labels = new[] { extra }, other_text = extra },
                },
            };
        }
        await AppServices.Client.CallAsync("ai_answer", new { result = payload });
    }
}
