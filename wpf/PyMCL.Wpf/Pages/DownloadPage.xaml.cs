using System.Windows;
using System.Windows.Controls;
using PyMCL.Services;

namespace PyMCL.Pages;

public partial class DownloadPage : UserControl
{
    public DownloadPage()
    {
        InitializeComponent();
        Loaded += async (_, _) =>
        {
            try
            {
                var el = await AppServices.Client.CallAsync("get_instances");
                var names = new List<string>();
                if (el.ValueKind == System.Text.Json.JsonValueKind.Array)
                {
                    foreach (var x in el.EnumerateArray())
                    {
                        if (x.ValueKind == System.Text.Json.JsonValueKind.String) names.Add(x.GetString() ?? "");
                        else if (x.TryGetProperty("name", out var n)) names.Add(n.GetString() ?? "");
                    }
                }
                InstanceBox.ItemsSource = names;
                if (InstanceBox.Items.Count > 0) InstanceBox.SelectedIndex = 0;
            }
            catch (Exception ex) { Hint.Text = ex.Message; }
        };
    }

    private async void Download_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var inst = InstanceBox.SelectedItem as string ?? "default";
            var ver = VersionBox.Text?.Trim() ?? "";
            var loader = (LoaderBox.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "vanilla";
            if (string.IsNullOrEmpty(ver)) { MessageBox.Show("请填写版本"); return; }
            // RPC 方法叫 install_game（download_version 从来不存在）；
            // “vanilla” 在两个桥里都写作 “无”。
            var tid = await AppServices.Client.StartTaskAsync("install_game", new
            {
                instance = inst,
                version = ver,
                loader = loader == "vanilla" ? "无" : loader,
            });
            Hint.Text = "任务已排队: " + tid;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "下载失败");
        }
    }
}
