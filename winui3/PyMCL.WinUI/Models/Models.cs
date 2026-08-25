using System.Text.Json.Serialization;

namespace PyMCL.Models;

public sealed class InstanceInfo
{
    public string Name { get; set; } = "";
    public int Versions { get; set; }
    public string Mc { get; set; } = "";
    public string Pack { get; set; } = "";
    [JsonPropertyName("pack_version")] public string PackVersion { get; set; } = "";
    [JsonPropertyName("mc_version")] public string McVersion { get; set; } = "";
    public string Java { get; set; } = "";
    [JsonPropertyName("java_label")] public string JavaLabel { get; set; } = "";
}

public sealed class VersionRow
{
    public string Version { get; set; } = "";
    public string Type { get; set; } = "";
    public string Date { get; set; } = "";
}

public sealed class CatalogItem
{
    public string Name { get; set; } = "";
    public string Author { get; set; } = "";
    public long Downloads { get; set; }
    public object? Id { get; set; }
    public string? Slug { get; set; }
    public string Source { get; set; } = "";
    public string Description { get; set; } = "";
    public List<string>? Tags { get; set; }
    public string Updated { get; set; } = "";
}

public sealed class JavaInfo
{
    public string Name { get; set; } = "";
    public string Major { get; set; } = "";
    public string Path { get; set; } = "";
}

public sealed class JavaOption
{
    public string Label { get; set; } = "";
    public string Value { get; set; } = "";
}

public sealed class SettingsDto
{
    [JsonPropertyName("share_libraries")] public bool ShareLibraries { get; set; }
    [JsonPropertyName("share_assets")] public bool ShareAssets { get; set; }
    [JsonPropertyName("download_threads")] public int DownloadThreads { get; set; }
    [JsonPropertyName("default_memory_mb")] public int DefaultMemoryMb { get; set; }
    [JsonPropertyName("default_resolution")] public List<int> DefaultResolution { get; set; } = new() { 854, 480 };
    [JsonPropertyName("ms_client_id")] public string MsClientId { get; set; } = "";
    [JsonPropertyName("curseforge_api_key")] public string CurseforgeApiKey { get; set; } = "";
    [JsonPropertyName("ai_mode")] public string AiMode { get; set; } = "public";
    [JsonPropertyName("ai_gateway_url")] public string AiGatewayUrl { get; set; } = "";
    [JsonPropertyName("ai_base_url")] public string AiBaseUrl { get; set; } = "";
    [JsonPropertyName("ai_api_key")] public string AiApiKey { get; set; } = "";
    [JsonPropertyName("ai_model")] public string AiModel { get; set; } = "deepseek-v4-flash";
    public string Root { get; set; } = "";
    [JsonPropertyName("default_isolation")] public string DefaultIsolation { get; set; } = "none";
    [JsonPropertyName("default_jvm_args")] public string DefaultJvmArgs { get; set; } = "";
    [JsonPropertyName("update_url")] public string UpdateUrl { get; set; } = "";
    [JsonPropertyName("download_source")] public string DownloadSource { get; set; } = "auto";
    [JsonPropertyName("launcher_visibility")] public string LauncherVisibility { get; set; } = "keep";
    [JsonPropertyName("gc_preset")] public string GcPreset { get; set; } = "auto";
    [JsonPropertyName("download_limit_kbps")] public int DownloadLimitKbps { get; set; }
    [JsonPropertyName("auto_check_update")] public bool AutoCheckUpdate { get; set; } = true;
    [JsonPropertyName("custom_homepage")] public string CustomHomepage { get; set; } = "";
    [JsonPropertyName("homepage_mode")] public string HomepageMode { get; set; } = "news";
    [JsonPropertyName("window_mode")] public string WindowMode { get; set; } = "window";
    [JsonPropertyName("game_dir")] public string GameDir { get; set; } = "";
    [JsonPropertyName("ui_fly_animation")] public bool UiFlyAnimation { get; set; } = true;
    [JsonPropertyName("ui_fly_duration_ms")] public int UiFlyDurationMs { get; set; } = 620;
}

public sealed class VersionSettingsDto
{
    public string Isolation { get; set; } = "none";
    [JsonPropertyName("memory_mb")] public int? MemoryMb { get; set; }
    [JsonPropertyName("jvm_args")] public string JvmArgs { get; set; } = "";
    public string Server { get; set; } = "";
    public string Port { get; set; } = "";
    [JsonPropertyName("pre_launch")] public string PreLaunch { get; set; } = "";
    [JsonPropertyName("post_launch")] public string PostLaunch { get; set; } = "";
    [JsonPropertyName("login_account")] public string LoginAccount { get; set; } = "";
    [JsonPropertyName("nide8_id")] public string Nide8Id { get; set; } = "";
    public string Gc { get; set; } = "";
    [JsonPropertyName("window_title")] public string WindowTitle { get; set; } = "";
    // 空串 = 跟随全局 window_mode（version_settings.DEFAULTS 同约定）
    [JsonPropertyName("window_mode")] public string WindowMode { get; set; } = "";
    [JsonPropertyName("pre_launch_wait")] public bool PreLaunchWait { get; set; } = true;
    [JsonPropertyName("offline_skin")] public string OfflineSkin { get; set; } = "default";
}

public sealed class NewsRow
{
    public string Title { get; set; } = "";
    public string Body { get; set; } = "";
    public string Version { get; set; } = "";
}

public sealed class AuthlibPreset
{
    public string Name { get; set; } = "";
    public string Api { get; set; } = "";
}

public sealed class AccountRow
{
    public string Name { get; set; } = "";
    public string Type { get; set; } = "";
    public string Uuid { get; set; } = "";
    public string Api { get; set; } = "";
    public string Avatar { get; set; } = "";
    public string Body { get; set; } = "";
    public bool Active { get; set; }
}

public sealed class TerracottaSnap
{
    public bool Supported { get; set; }
    public bool Installed { get; set; }
    public bool Running { get; set; }
    public string State { get; set; } = "";
    public string Label { get; set; } = "";
    public string Room { get; set; } = "";
    public string Url { get; set; } = "";
    public string Error { get; set; } = "";
}

public sealed class ModEntry
{
    public string Filename { get; set; } = "";
    public bool Enabled { get; set; } = true;
}

public sealed class AiStoreDto
{
    [JsonPropertyName("active_id")] public string ActiveId { get; set; } = "";
    public List<AiChatDto> Chats { get; set; } = new();
}

public sealed class AiChatDto
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public List<AiMsgDto> Messages { get; set; } = new();
}

public sealed class AiMsgDto
{
    public string Role { get; set; } = "";
    public string Content { get; set; } = "";
}

public sealed class TaskItem
{
    public string TaskId { get; set; } = "";
    public string Title { get; set; } = "";
    public string Status { get; set; } = "排队中…";
    public string Speed { get; set; } = "";
    public string Log { get; set; } = "";
    public double Progress { get; set; }
    public bool Finished { get; set; }
    public bool Success { get; set; }
    public bool Expanded { get; set; }
}

public sealed class CrashReport
{
    public string Title { get; set; } = "";
    public string Headline { get; set; } = "";
    public string Summary { get; set; } = "";
    public string Detail { get; set; } = "";
    public string Help { get; set; } = "";
    [JsonPropertyName("direct_file")] public string DirectFile { get; set; } = "";
    [JsonPropertyName("exit_code")] public int? ExitCode { get; set; }
    [JsonPropertyName("exit_hint")] public string ExitHint { get; set; } = "";
    [JsonPropertyName("task_id")] public string TaskId { get; set; } = "";
    public string Instance { get; set; } = "";
    public string Version { get; set; } = "";
    public List<CrashAction>? Actions { get; set; }
}

public sealed class CrashAction
{
    public string Id { get; set; } = "";
    public string Label { get; set; } = "";
    public List<string>? Mods { get; set; }
    public int? Major { get; set; }
    [JsonPropertyName("memory_mb")] public int? MemoryMb { get; set; }
    public string Instance { get; set; } = "";
    public string Version { get; set; } = "";
}

public sealed class PreflightResult
{
    public bool Ok { get; set; }
    public List<PreflightItem> Items { get; set; } = new();
}

public sealed class PreflightItem
{
    public string Level { get; set; } = "";
    public string Code { get; set; } = "";
    public string Title { get; set; } = "";
    public string Detail { get; set; } = "";
}

public sealed class CrashActionResult
{
    public bool Ok { get; set; }
    public string Message { get; set; } = "";
    [JsonPropertyName("task_id")] public string? TaskId { get; set; }
}

public sealed class BridgeEvent
{
    public string Event { get; set; } = "";
    public string TaskId { get; set; } = "";
    public string Title { get; set; } = "";
    public int Current { get; set; }
    public int Total { get; set; }
    public string Message { get; set; } = "";
    public string Text { get; set; } = "";
    public bool Success { get; set; }
    public int Count { get; set; }
    public string Code { get; set; } = "";
    public string Uri { get; set; } = "";
    public string Detail { get; set; } = "";
    public string Kind { get; set; } = "";
    public string Label { get; set; } = "";
    public string Name { get; set; } = "";
    public string PayloadJson { get; set; } = "";
    public bool Stopped { get; set; }
    public CrashReport? Crash { get; set; }
}

public sealed class CatalogFile
{
    public object? Id { get; set; }
    public string Name { get; set; } = "";
    [JsonPropertyName("version_number")] public string VersionNumber { get; set; } = "";
    public string Filename { get; set; } = "";
    [JsonPropertyName("game_versions")] public List<string>? GameVersions { get; set; }
    public List<string>? Loaders { get; set; }
    public string Date { get; set; } = "";
    public long Downloads { get; set; }
    [JsonPropertyName("release_type")] public string ReleaseType { get; set; } = "";
    public string Source { get; set; } = "";
}

public sealed class LoaderVer
{
    public string Label { get; set; } = "";
    public string Id { get; set; } = "";
}

public sealed class HelpArticle
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public string Body { get; set; } = "";
}

public sealed class CatalogKind
{
    public string Title { get; set; } = "";
    public string SearchTitle { get; set; } = "";
    public string SearchMethod { get; set; } = "";
    public string InstallMethod { get; set; } = "";
    public string EmptySearch { get; set; } = "";
    public string LinkLabel { get; set; } = "";
    public string LinkTitle { get; set; } = "";
    public string LinkHint { get; set; } = "";
    public string LocalLabel { get; set; } = "";
    public string LocalFilter { get; set; } = "";
    public bool IsModpack { get; set; }
    public string[] Types { get; set; } = Array.Empty<string>();
    public string FileKind { get; set; } = "mod";
    public string DefaultSource { get; set; } = "";

    public static CatalogKind Mod { get; } = new()
    {
        Title = "Mod",
        SearchTitle = "搜索 Mod",
        SearchMethod = "search_mods",
        InstallMethod = "install_mod",
        EmptySearch = "没有找到相关模组",
        LinkLabel = "从链接安装",
        LinkTitle = "从链接安装模组",
        LinkHint = "模组下载链接 (URL)",
        LocalLabel = "导入 jar",
        LocalFilter = ".jar",
        Types = new[] { "全部", "优化", "科技", "魔法", "冒险" },
    };

    public static CatalogKind Modpack { get; } = new()
    {
        Title = "整合包",
        SearchTitle = "搜索整合包",
        SearchMethod = "search_modpacks",
        InstallMethod = "install_modpack",
        EmptySearch = "没有找到相关整合包",
        LinkLabel = "从链接安装",
        LinkTitle = "从链接安装整合包",
        LinkHint = "整合包链接或文件",
        LocalLabel = "导入文件",
        LocalFilter = ".mrpack;.zip",
        IsModpack = true,
        Types = new[] { "全部", "生存", "空岛", "科技", "魔法" },
        FileKind = "modpack",
    };

    public static CatalogKind Datapack { get; } = new()
    {
        Title = "数据包",
        SearchTitle = "搜索数据包",
        SearchMethod = "search_datapacks",
        InstallMethod = "install_datapack",
        EmptySearch = "没有找到相关数据包",
        LinkLabel = "从链接安装",
        LinkTitle = "从链接安装数据包",
        LinkHint = "数据包下载链接 (URL)",
        LocalLabel = "导入 zip",
        LocalFilter = ".zip",
        Types = new[] { "全部", "生存", "冒险", "装饰" },
        FileKind = "datapack",
    };

    public static CatalogKind ResourcePack { get; } = new()
    {
        Title = "资源包",
        SearchTitle = "搜索资源包",
        SearchMethod = "search_resourcepacks",
        InstallMethod = "install_resourcepack",
        EmptySearch = "没有找到相关资源包",
        LinkLabel = "从链接安装",
        LinkTitle = "从链接安装资源包",
        LinkHint = "资源包下载链接 (URL)",
        LocalLabel = "导入 zip",
        LocalFilter = ".zip",
        Types = new[] { "全部", "16x", "32x", "64x", "写实", "现代风", "动态效果" },
        FileKind = "resourcepack",
    };

    public static CatalogKind Shader { get; } = new()
    {
        Title = "光影包",
        SearchTitle = "搜索光影包",
        SearchMethod = "search_shaders",
        InstallMethod = "install_shader",
        EmptySearch = "没有找到相关光影",
        LinkLabel = "从链接安装",
        LinkTitle = "从链接安装光影",
        LinkHint = "光影包下载链接 (URL)",
        LocalLabel = "导入 zip",
        LocalFilter = ".zip",
        Types = new[] { "全部", "写实", "卡通", "高性能", "光追" },
        FileKind = "shader",
    };

    public static CatalogKind World { get; } = new()
    {
        Title = "世界",
        SearchTitle = "搜索世界",
        SearchMethod = "search_worlds",
        InstallMethod = "install_world",
        EmptySearch = "没有找到相关世界",
        LinkLabel = "从链接安装",
        LinkTitle = "从链接安装世界",
        LinkHint = "世界下载链接 (URL)",
        LocalLabel = "导入 zip",
        LocalFilter = ".zip",
        Types = new[] { "全部", "生存", "冒险", "创造" },
        FileKind = "world",
        DefaultSource = "CurseForge",
    };
}

public sealed class ServerRow
{
    public string Name { get; set; } = "";
    public string Ip { get; set; } = "";
    public int Port { get; set; } = 25565;
    public string Description { get; set; } = "";
}

public sealed class PlaytimeRow
{
    public string Instance { get; set; } = "";
    public int TotalSeconds { get; set; }
    public string TotalText { get; set; } = "";
    public string VersionsText { get; set; } = "";
}
