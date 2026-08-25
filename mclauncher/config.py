# -*- coding: utf-8 -*-
"""启动器全局配置。"""
from pathlib import Path

from . import utils

CONFIG_FILE = utils.ROOT / "config.json"

DEFAULT_CONFIG = {
    # 实例目录名（相对于启动器主目录）。与 PCL/HMCL 一样用 .minecraft
    "instances_dir": ".minecraft",
    "default_instance": "default",
    # 外部游戏目录注册表 {实例名: 绝对路径}：把已有 .minecraft 原地当实例用，
    # 不复制文件（对齐 HMCL「游戏目录」/ PCL 的「添加已有文件夹」）
    "external_instances": {},
    # Java 运行时目录名（所有实例共享）
    "java_dir": "java",
    # 版本隔离选项：False 时每个实例拥有独立的 libraries/assets；True 时共享以节省空间
    "shared_libraries": False,
    "shared_assets": False,
    # 默认分配内存 (MB)
    "memory_mb": 4096,
    # 下载并发线程数
    "download_threads": 8,
    # 默认窗口分辨率
    "width": 854,
    "height": 480,
    # 微软 OAuth 客户端 ID（可替换为自己的应用 ID）
    "microsoft_client_id": "00000000402b5328",
    # CurseForge 官方 API key
    # 默认值来自 HMCL 开源分析（已实测可用），商业产品应自行申请
    "curseforge_api_key": "$2a$10$o8pygPrhvKBHuuh5imL2W.LCNFhB15zBYAExXx/TqTx/Zp5px2lxu",
    # GitHub raw/releases 国内镜像前缀（会拼在原始 https URL 前面）
    "github_proxy_prefixes": [
        "https://gitproxy.mrhjx.cn/",
        "https://ghproxy.vip/",
        "https://gh-proxy.com/",
        "https://v6.gh-proxy.org/",
        "https://cdn.gh-proxy.com/",
    ],
    # 每次启动是否强制刷新远程版本清单
    "force_manifest_refresh": False,
    # 文件下载源：auto=官方<4s用官方否则BMCLAPI；official；bmclapi
    "download_source": "auto",
    # 模组/整合包：auto / official / mcim
    "community_source": "auto",
    # 跟随系统代理（Clash 7897）。关了才强制直连
    "use_system_proxy": True,
    # AI 助手：public=公益网关；custom=用户自己的 NewAPI
    "ai_mode": "public",
    "ai_gateway_url": "",
    "ai_base_url": "",
    "ai_api_key": "",
    "ai_model": "deepseek-v4-flash",
    # AI 权限：写操作是否逐条弹确认；standard=全部确认 full=只确认破坏性操作
    "ai_confirm_writes": True,
    "ai_permission_mode": "standard",
    # HMCL 自定义 EasyTier 会合节点（官方 /nodes 表往往不够）
    "terracotta_extra_nodes": [
        "https://terracotta.glavo.site/acebc7d8-1208-47fd-b212-d03ac49e36e0",
    ],
    # 反馈中心：空则用 DEFAULT_FEEDBACK_URL / 环境变量 PYMCL_FEEDBACK_URL
    "feedback_url": "",
    "feedback_heartbeat": True,
    "feedback_consent": None,
    "device_id": "",
    "default_isolation": "none",
    "default_jvm_args": "",
    # 全局包装器命令：版本设置未填 wrapper 时兜底（对齐 HMCL 的「包裹命令」）
    "wrapper_command": "",
    "default_priority": "normal",
    "update_url": "https://pymcl.dev/update.json",
    "theme_color": "#2E9B6B",
    "ui_dark": False,
    "ui_background": "",
    # 这三个键以前只在 save_settings 里写、没在这儿声明。
    # `save()` 落的是整份 data，`load()` 却只按本表的键名回读 ——
    # 结果就是「关掉飞入动画、重开启动器它又自己回来了」。
    "ui_fly_animation": True,
    "ui_fly_duration_ms": 620,
    # 全局默认 Java：版本设置与实例偏好都是「自动」时才生效
    "default_java": "",
    "global_mods_dir": "",
    "launcher_visibility": "keep",
    "gc_preset": "auto",
    "download_limit_kbps": 0,
    "auto_check_update": True,
    "custom_homepage": "",
    "homepage_mode": "news",
    "window_mode": "window",
    "skip_assets": False,
    "first_run": True,
    "show_hidden_versions": False,
    "catalog_favorites": [],
    "offline_skin": "default",
    "allow_multi_instance": False,
    "language": "zh_CN",
}


class Config:
    def __init__(self):
        self.data = dict(DEFAULT_CONFIG)
        # 每次改动 +1。上层（如 app.backend.get_setting）据此判断缓存是否还新鲜，
        # 不用每取一个键就把整份设置字典重建一遍。
        self.revision = 0
        self.load()

    def load(self):
        self.revision += 1
        stored = utils.read_json(CONFIG_FILE, None)
        if isinstance(stored, dict):
            missing = False
            for k in DEFAULT_CONFIG:
                if k in stored:
                    self.data[k] = stored[k]
                else:
                    missing = True
            if self._migrate_legacy_instances_dir():
                missing = True
            if missing:
                self.save()
        else:
            self._migrate_legacy_instances_dir()
            self.save()

    def _migrate_legacy_instances_dir(self) -> bool:
        """旧版默认 instances/ 迁到 .minecraft/。用户自己改过的路径不动。"""
        current = str(self.data.get("instances_dir") or "").strip()
        if current != "instances":
            return False
        old = utils.ROOT / "instances"
        new = utils.ROOT / ".minecraft"
        if old.is_dir() and not new.exists():
            old.rename(new)
        self.data["instances_dir"] = ".minecraft"
        return True

    def save(self):
        utils.write_json(CONFIG_FILE, self.data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.revision += 1

    def update(self, mapping):
        self.data.update(mapping)
        self.revision += 1

    # ---- 路径 ----
    @property
    def instances_dir(self) -> Path:
        return utils.ROOT / str(self.data["instances_dir"])

    @property
    def java_dir(self) -> Path:
        return utils.ROOT / str(self.data["java_dir"])

    @property
    def cache_dir(self) -> Path:
        return utils.ROOT / "cache"

    def libraries_dir(self, instance_dir: Path) -> Path:
        if self.data["shared_libraries"]:
            return utils.ROOT / "shared" / "libraries"
        return Path(instance_dir) / "libraries"

    def assets_dir(self, instance_dir: Path) -> Path:
        if self.data["shared_assets"]:
            return utils.ROOT / "shared" / "assets"
        return Path(instance_dir) / "assets"


# 全局单例
CONFIG = Config()
