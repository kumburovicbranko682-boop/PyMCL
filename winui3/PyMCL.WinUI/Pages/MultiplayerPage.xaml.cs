using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using PyMCL.Models;
using PyMCL.Services;

namespace PyMCL.Pages;

public sealed partial class MultiplayerPage : UserControl
{
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(800) };
    private bool _preparing;

    public MultiplayerPage()
    {
        InitializeComponent();
        _timer.Tick += async (_, _) => await ReloadAsync();
        Loaded += (_, _) => _timer.Start();
        Unloaded += (_, _) => _timer.Stop();
    }

    public async Task ReloadAsync()
    {
        if (AppServices.Client is null) return;
        try
        {
            LanHint.Text = await AppServices.Client.CallAsync<string>("lan_hint") ?? "";
            var snap = await AppServices.Client.CallAsync<TerracottaSnap>("terracotta_snapshot");
            if (snap is null) return;
            StateLabel.Text = string.IsNullOrEmpty(snap.Error) ? snap.Label : snap.Error;
            RoomLabel.Text = string.IsNullOrEmpty(snap.Room) ? "" : "邀请码 " + snap.Room;
            EnterWorldButton.Visibility = snap.State == "guest-ok" && !string.IsNullOrEmpty(snap.Url)
                ? Visibility.Visible : Visibility.Collapsed;
            if (snap.Supported && !snap.Installed && !_preparing)
            {
                _preparing = true;
                AppServices.FlyToTasks?.Invoke(this, "联", "#0B6E99");
                await AppServices.Client.StartTaskAsync("terracotta_prepare");
            }
        }
        catch (Exception ex)
        {
            StateLabel.Text = ex.Message;
        }
    }

    private async void Host_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try { await AppServices.Client.CallAsync("terracotta_host"); await ReloadAsync(); }
        catch (Exception ex) { AppServices.Toast?.Invoke("开房失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void Join_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        var room = JoinBox.Text?.Trim() ?? "";
        if (string.IsNullOrEmpty(room))
        {
            AppServices.Toast?.Invoke("缺少邀请码", "请填写好友房间码", InfoBarSeverity.Warning);
            return;
        }
        try { await AppServices.Client.CallAsync("terracotta_join", new { room }); await ReloadAsync(); }
        catch (Exception ex) { AppServices.Toast?.Invoke("加入失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void Enter_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try
        {
            var result = await AppServices.Client.CallAsync<string>("terracotta_enter_world") ?? "";
            if (result.StartsWith("task-", StringComparison.Ordinal))
            {
                AppServices.FlyToTasks?.Invoke(this, "进", "#2E9B6B");
                AppServices.Toast?.Invoke("正在启动游戏", "启动后会直接进入陶瓦联机大厅。", InfoBarSeverity.Success);
            }
            else
            {
                AppServices.Toast?.Invoke("已加入房间", string.IsNullOrEmpty(result) ? "请到多人游戏双击「陶瓦联机大厅」。" : result, InfoBarSeverity.Success);
            }
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("进入世界失败", ex.Message, InfoBarSeverity.Error); }
    }

    private async void Fw_Click(object sender, RoutedEventArgs e)
    {
        if (AppServices.Client is null) return;
        try
        {
            var msg = await AppServices.Client.CallAsync<string>("terracotta_allow_firewall");
            AppServices.Toast?.Invoke("防火墙", msg ?? "已请求放行", InfoBarSeverity.Success);
        }
        catch (Exception ex) { AppServices.Toast?.Invoke("防火墙", ex.Message, InfoBarSeverity.Error); }
    }
}
