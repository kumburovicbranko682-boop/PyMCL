# PyMCL —— 全版本 Minecraft 启动器（Python）

一个用纯 Python 编写的 Minecraft 启动器，支持从远古版本（rd-132211 / Alpha / Beta）到最新快照的**全部 Minecraft 版本**，具备**联网下载**、**整合包安装**、**Java 自动匹配下载**、**版本隔离**与**内置 AI 助手**。

## ✨ 功能

- 🎮 **全版本支持**：通过 Mojang 官方版本清单安装并启动任何版本（含远古 Alpha/Beta、1.6 之前的旧版资源布局、Forge 处理器等特殊处理）。
- 🌐 **联网下载**：多线程（默认 8 线程）下载版本 JSON、客户端 jar、依赖库、natives、资源文件，带 sha1/sha512 校验、失败重试与进度显示。
- 📦 **整合包**：
  - Modrinth：在线搜索、版本选择、一键安装 `.mrpack`（自动装 MC 版本 + Fabric/Forge/Quilt/NeoForge 加载器 + 全部 Mod + 覆盖文件）；
  - CurseForge：直接安装整合包 zip（本地文件或直链）。
- 🧩 **模组（单 Mod）**：
  - Modrinth / CurseForge 双源在线搜索（CurseForge 走 BMCLAPI 国内镜像，无需 API key、速度快），自动匹配实例的 MC 版本与加载器，连带下载必需依赖（如 Fabric API）；
  - 支持 CurseForge 模组链接、.jar 直链与本地 jar 导入；
  - 已安装模组列表与删除。
- ☕ **Java 自动管理**：按版本要求自动下载 Mojang 官方 Java 运行时（精确匹配）或 Adoptium Temurin（Java 8/11/17/21），也可手动下载/指定。
- 🗂️ **版本隔离**：每个“实例”都是一个独立的 `.minecraft`（拥有自己的 versions/libraries/assets/mods/saves），互不干扰；可选共享 libraries/assets 以节省磁盘空间。
- 🔐 **账号系统**：离线模式 + 微软正版登录（设备代码流，自动刷新令牌）。
- 🖥️ **图形界面 + 命令行**：PySide6 Fluent UI（启动 / 版本 / 整合包 / 模组 / Java / 实例 / **AI 助手** / 设置等），以及完整的 CLI。
- 🎨 **自定义 UI 布局**：启动页是自由画布——卡片（启动横幅 / 启动配置 / 实时日志 / 新闻主页 / 快捷入口 / 便签 / 游戏时长 / 任务摘要）可任意**拖动、八向缩放、增删**，网格吸附可调可关；布局按比例随窗口自适应，支持**多套方案**切换与 JSON **导入/导出**分享；侧栏条目可**直接拖动重排**（一级项与固定子页自由混排）；侧栏可**显隐条目、拖动右缘调宽**；「下载」「更多」两个分区里的**子页归属与顺序**随意调（比如把账号挪进下载栏、把 Java 挪到更多）；分区里的任意子页还能**直接拖到侧栏变成一级入口**（移动语义：拖走后原分区不再显示，拖回分区横条即放回），分区横条和侧栏之间双向拖拽。
- 🤖 **AI 助手**：对话里下游戏、装模组/光影/整合包、更新模组、读崩溃日志、扫模组冲突、调内存、改模组配置，还能查 Minecraft Wiki；崩溃弹窗/预检失败/任务失败/模组页都可一键「问 AI」并自动带上现场信息，诊断结果附一键修复按钮。写操作按权限设置弹确认。公益接口走自建网关（默认 `deepseek-v4-flash`，白名单内可切换），也可接自定义 NewAPI。
- 🧩 **加载器**：Fabric / Quilt（官方 meta 版本 JSON）、Forge / NeoForge（官方安装器）。
- 🪟 跨平台：Windows / macOS / Linux。
- 📱 **Android**：仓库内有 `android/` Compose 骨架（`0.1.0-skeleton`），可编译浏览 UI；**尚不能**像桌面端 / PCL 一样完整装版与启动游戏，详见 `android/ANDROID.md`。

## 📥 安装

要求 Python 3.9+。

```bash
pip install -r requirements.txt
```

## 📦 打包为 exe（点开即用，免装 Python）

无需手动配置，项目自带打包脚本：

```bat
:: Windows：双击 build_exe.bat（自动安装 PyInstaller 并打包）
build_exe.bat
```

```bash
# Linux / macOS
bash build_exe.sh
```

打包产物：

| 文件 | 说明 |
| --- | --- |
| `dist/PyMCL.exe` | 图形界面版，双击即用（无黑色控制台窗口，**推荐**） |
| `dist/PyMCL-CLI.exe` | 命令行版（带控制台，供 `install`/`launch` 等命令使用） |

特性与说明：

- **便携模式**：配置、实例、账号、下载的 Java 都保存在 exe 所在目录旁边，整个文件夹拷到 U 盘/别的电脑都能直接用；若 exe 放在不可写目录（如 Program Files），数据会自动存到 `%APPDATA%\PyMCL`；
- **自定义图标**：把图标文件改名为 `icon.ico`（Windows）放到项目目录再打包即可；
- **杀毒软件误报**：PyInstaller 单文件 exe 偶尔被误报，加白名单即可；介意的话把 `build_exe.bat` 里的 `--onefile` 改成 `--onedir`（文件夹版，误报更少，需整个文件夹一起拷贝）；
- 打包机与运行机无需同版本 Python，目标电脑**不需要安装 Python**。

### 从 GitHub Actions 下载现成 exe

仓库已配置 **Build Windows EXE** 工作流（Windows 托管机 + PyInstaller）。打开 GitHub 的 **Actions** 页，选一次成功运行，在 Artifacts 里下载 `PyMCL-windows-x64`（内含 `PyMCL.exe` 与 `PyMCL-CLI.exe`）。也可以在该工作流页手动点 **Run workflow**。

## 🚀 快速开始

### 图形界面

```bash
python main.py
```

1. **实例**页创建实例（或直接使用默认实例）；
2. **版本**页双击一个版本即可下载安装（支持关键字过滤）；
3. **启动**页填用户名，点“🚀 启动游戏”（Java 自动匹配，没有会自动下载）；
4. **AI 助手**页直接说要做什么，例如「下一款游戏 1.20.1 Fabric」「装钠和光影」「启动闪退了帮我看」。

### 命令行

```bash
# 列出可下载版本（可加关键字过滤）
python main.py versions 1.21

# 安装版本（默认装到 default 实例）
python main.py install 1.21.4
python main.py install b1.7.3 -i 怀旧服        # 远古版本装到独立实例

# 安装加载器
python main.py install-fabric 1.20.1
python main.py install-forge 1.16.5 -i 工业

# 启动（离线）
python main.py launch 1.21.4 -u Steve

# 微软正版登录后启动
python main.py login
python main.py launch 1.21.4 --account 你的正版名

# 搜索并安装 Modrinth 整合包
python main.py search 优化
python main.py modpack https://cdn.modrinth.com/data/xxx/versions/xxx.mrpack -i 新整合包
python main.py modpack D:/下载/某个CurseForge整合包.zip -i 新整合包

# 模组：搜索 / 安装 / 列表
python main.py mods search sodium
python main.py mods install sodium -i 1.20.1实例
python main.py mods install https://modrinth.com/mod/jei -i 1.20.1实例
python main.py mods install D:/下载/某个模组.jar -i 1.20.1实例
python main.py mods list

# Java 管理
python main.py java list
python main.py java install 17

# 实例（版本隔离）管理
python main.py instance create 红石
python main.py instance list
```

## 🤖 AI 助手

侧栏打开 **AI 助手**；崩溃弹窗、启动预检失败、下载任务失败提示、模组管理页（按钮 + 右键菜单）也都能一键「问 AI」，会自动把现场信息（日志尾巴、实例、版本、报错）带进对话。

能做的事：

| 能力 | 说明 |
| --- | --- |
| 装游戏 / 加载器 | 原版，以及 Fabric / Quilt / Forge / NeoForge |
| 搜装内容 | 模组、整合包、光影、资源包、数据包（中文名可搜）；点名的东西精确命中时直接装，不多问 |
| 管模组 | 列出 / 启用 / 禁用 / 删除 / **检查并更新到新版本** |
| 排错 | 读 `latest.log` 和崩溃报告，判断原因，并给出**可点的一键修复按钮**（禁用嫌疑模组 / 提内存 / 下 Java / 修复版本文件） |
| 冲突 | 扫描重复模组、缺依赖、加载器不匹配 |
| 改配置 | 读改实例 `config` 下的模组配置（先备份 `.bak`）；调内存、给实例指定 Java |
| 任务追踪 | 安装任务在后台跑；失败会自动回写到对话并给「让 AI 分析并重试」按钮 |
| 知识 | 启动器用法查内置帮助；游戏玩法查 Minecraft Wiki（官方公开 API），不凭记忆编 |
| 其它 | 看/下 Java、建实例、修复版本、启动游戏、查任务状态 |

写操作（安装、删除、改配置、启动）默认会先让你确认；在选择题里点过的那一项会直接执行，不再重复确认（删除类操作除外）。助手记得常用实例、最近装过什么、机器内存等少量偏好（存本机 `ai_memory.json`，不含账号信息）。

各端能力：桌面 PySide6 版功能最全（上表全部）；EziApp / WinUI3 共用同一 AI 内核（对话、确认、选择、流式），但暂无一键修复卡和任务失败回写；**Android 端 AI 尚未接入**（界面已明示禁用），见 `android/ANDROID.md`。

### 公益接口与网关（发行前必读）

公益模式**只走网关**：启动器内不再内嵌任何上游令牌（旧版的混淆令牌已退役），`sk-` 只放在网关机器的环境变量里。部署步骤见 `ai_gateway/README.md`，要点：

1. 公网机器跑 `ai_gateway/server.py`，配好 HTTPS（客户端拒绝非内网明文 HTTP）；
2. 把网关地址填进 `mclauncher/ai/defaults.py` 的 `DEFAULT_GATEWAY_URL`（或打包时设 `PYMCL_AI_GATEWAY` 环境变量）；
3. 想开放「深度诊断」模型档位，把模型名加进网关的 `NEWAPI_ALLOWED_MODELS` 白名单。

```bash
cd ai_gateway
copy .env.example .env
python server.py
```

健康检查：`GET /health`（含模型白名单）。对话口：`POST /pymcl/chat`（请求头必须带 `X-PyMCL-Client: PyMCL/x.y.z`）。

## 🗂️ 目录结构与版本隔离

```
PyMCL/                      ← 启动器主目录（可用环境变量 PYMCL_HOME 移动）
├── main.py                 ← 入口（CLI / GUI）
├── mclauncher/             ← 核心包（含 mclauncher/ai 助手）
├── ai_gateway/             ← 可选：公益 AI 网关（令牌不进启动器）
├── config.json             ← 全局设置（本地生成，不进仓库）
├── accounts.json           ← 登录账号缓存
├── cache/                  ← 版本清单等缓存
├── java/                   ← 自动下载的 Java 运行时（按版本区分）
└── .minecraft/             ← 与 PCL/HMCL 相同的实例根目录
    ├── default/            ← 实例 1（完全独立）
    │   ├── versions/1.21.4/...
    │   ├── libraries/  assets/  mods/  saves/  config/ ...
    │   └── .instance.json
    └── 工业/               ← 实例 2（完全独立，互不影响）
```

- **完全隔离（默认）**：每个实例各自拥有 versions/libraries/assets，删除实例即可彻底清理；
- **共享模式**：在“设置”中勾选共享 libraries/assets，多个实例复用同一份文件以节省空间；
- 每个版本在 `versions/<版本id>/` 下还有独立目录（jar、natives 等），这是 Minecraft 本身的版本隔离。

## ☕ Java 匹配规则

| Minecraft 版本 | 使用的 Java |
| --- | --- |
| ≤ 1.16 | Java 8（远古版本在 Windows 上使用 32 位 Java 8） |
| 1.17 ~ 1.20.4 | Java 17 |
| 1.20.5 ~ 1.21.11 | Java 21（Mojang 官方运行时，精确匹配） |
| 26.1+（年式版本号） | Java 25 |
| 快照 | 按 version JSON 的 component 自动选择 |

优先使用 Mojang 官方运行时（与官方启动器完全一致），失败时回退 Adoptium Temurin；也可在“Java”页手动下载或在“启动”页手动指定。

## 🔐 微软登录说明

采用设备代码流（Device Code Flow）：点击“微软登录”后按提示打开 `microsoft.com/link` 输入代码即可，无需输入密码。默认使用公开的 Xbox 客户端 ID（`00000000402b5328`），如失效可在“设置”中替换为自己的 Azure 应用 ID。

## ⚠️ 常见问题

- **远古版本闪退**：确认使用了 32 位 Java 8（启动器会自动下载）；
- **Forge 1.7.10~1.12.2 安装慢**：安装器由 Forge 官方提供，会自动下载大量文件；
- **整合包安装失败**：CurseForge 个别 Mod 禁止第三方下载会返回 403，属正常现象；Modrinth 包一般不受影响；
- **防火墙提示**：允许 Python 联网即可；
- **配置文件损坏**：删除 `config.json` 即可恢复默认。

## 📄 免责声明

本项目仅用于学习与技术交流。请支持正版 Minecraft，整合包与 Mod 版权归原作者所有。

---

> 注：本目录下的 `mc_launcher.py` / `mclauncher.py` 等是早期版本的本地扫描式启动器，与本文档描述的新版（`main.py` + `mclauncher/` 包）相互独立。
