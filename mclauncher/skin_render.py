# -*- coding: utf-8 -*-
"""本地皮肤 2D 立绘渲染（PCL2 / HMCL 皮肤预览同款，完全离线）。

直接从皮肤纹理合成正面全身像 / 头像，不访问任何网络：
- 现代 64x64 与旧版 64x32 皮肤都支持，HD 皮肤（128x128 等 64 的
  整数倍）按倍率放大源矩形。
- slim（3px 细手臂）/ classic（4px）两种模型。
- 外层（帽子/外套/袖子/裤腿）按透明度叠加；旧版皮肤没有四肢外层，
  左臂/左腿用右侧镜像补齐。旧版帽子层若整块完全不透明视为无帽子
  （老皮肤常拿这块区域当调色板/签名，不是真帽子）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import CONFIG


class SkinRenderError(Exception):
    pass


def _pil():
    try:
        from PIL import Image
        return Image
    except ImportError as e:  # pragma: no cover - 依赖装好后不会走到
        raise SkinRenderError(
            "缺少 Pillow 依赖，无法渲染皮肤预览。请重新执行 pip install -r requirements.txt"
        ) from e


# 现代 64x64 布局的正面矩形（基于 64 分辨率，HD 按倍率放大）。
# 每项: (src_x, src_y, w, h)
_HEAD = (8, 8, 8, 8)
_HAT = (40, 8, 8, 8)
_BODY = (20, 20, 8, 12)
_JACKET = (20, 36, 8, 12)
_RIGHT_LEG = (4, 20, 4, 12)
_RIGHT_LEG_OVER = (4, 36, 4, 12)
_LEFT_LEG = (20, 52, 4, 12)
_LEFT_LEG_OVER = (4, 52, 4, 12)


def _arm_rects(slim: bool) -> dict:
    w = 3 if slim else 4
    return {
        "right": (44, 20, w, 12),
        "right_over": (44, 36, w, 12),
        "left": (36, 52, w, 12),
        "left_over": (52, 52, w, 12),
        "w": w,
    }


def _load(src_path) -> tuple["object", int, bool]:
    """读皮肤纹理，返回 (RGBA image, 倍率 s, 是否旧版 64x32)。"""
    Image = _pil()
    p = Path(src_path)
    if not p.is_file():
        raise SkinRenderError(f"皮肤文件不存在: {p}")
    try:
        img = Image.open(p).convert("RGBA")
    except Exception as e:
        raise SkinRenderError(f"皮肤文件无法解析: {e}") from e
    w, h = img.size
    if w % 64 == 0 and w // 64 >= 1 and h == w:
        return img, w // 64, False
    if w % 64 == 0 and w // 64 >= 1 and h * 2 == w:
        return img, w // 64, True
    raise SkinRenderError(f"不支持的皮肤尺寸 {w}x{h}（应为 64x64、64x32 或其整数倍）")


def _crop(img, rect, s: int, mirror: bool = False):
    x, y, w, h = rect
    part = img.crop((x * s, y * s, (x + w) * s, (y + h) * s))
    if mirror:
        from PIL import Image
        part = part.transpose(Image.FLIP_LEFT_RIGHT)
    return part


def _fully_opaque(part) -> bool:
    alpha = part.getchannel("A")
    lo, _hi = alpha.getextrema()
    return lo == 255


def _paste_overlay(canvas, part, pos):
    """带透明度叠加到画布上（paste 的 alpha 语义会整块覆盖，得用 alpha_composite）。"""
    canvas.alpha_composite(part, dest=pos)


def render_front(src_path, model: str = "default", scale: int = 8):
    """正面全身像，返回 PIL Image（RGBA）。

    画布（64 基准）：宽 = 臂宽*2 + 8，高 = 32；输出按 scale 放大
    （最近邻，保持像素风）。
    """
    Image = _pil()
    img, s, legacy = _load(src_path)
    slim = (model or "").strip().lower() == "slim"
    arms = _arm_rects(slim)
    aw = arms["w"]
    cw, ch = (aw * 2 + 8), 32
    canvas = Image.new("RGBA", (cw * s, ch * s), (0, 0, 0, 0))

    def at(x, y):
        return (x * s, y * s)

    # 基础层
    canvas.paste(_crop(img, _HEAD, s), at(aw, 0))
    canvas.paste(_crop(img, _BODY, s), at(aw, 8))
    canvas.paste(_crop(img, arms["right"], s), at(0, 8))
    canvas.paste(_crop(img, _RIGHT_LEG, s), at(aw, 20))
    if legacy:
        canvas.paste(_crop(img, arms["right"], s, mirror=True), at(aw + 8, 8))
        canvas.paste(_crop(img, _RIGHT_LEG, s, mirror=True), at(aw + 4, 20))
    else:
        canvas.paste(_crop(img, arms["left"], s), at(aw + 8, 8))
        canvas.paste(_crop(img, _LEFT_LEG, s), at(aw + 4, 20))

    # 外层
    hat = _crop(img, _HAT, s)
    if not (legacy and _fully_opaque(hat)):
        _paste_overlay(canvas, hat, at(aw, 0))
    if not legacy:
        _paste_overlay(canvas, _crop(img, _JACKET, s), at(aw, 8))
        _paste_overlay(canvas, _crop(img, arms["right_over"], s), at(0, 8))
        _paste_overlay(canvas, _crop(img, arms["left_over"], s), at(aw + 8, 8))
        _paste_overlay(canvas, _crop(img, _RIGHT_LEG_OVER, s), at(aw, 20))
        _paste_overlay(canvas, _crop(img, _LEFT_LEG_OVER, s), at(aw + 4, 20))

    scale = max(1, int(scale))
    if scale != s:
        canvas = canvas.resize((cw * scale, ch * scale), Image.NEAREST)
    return canvas


def render_head(src_path, scale: int = 8):
    """头像（头 + 帽子层），返回 PIL Image（RGBA）。"""
    Image = _pil()
    img, s, legacy = _load(src_path)
    canvas = Image.new("RGBA", (8 * s, 8 * s), (0, 0, 0, 0))
    canvas.paste(_crop(img, _HEAD, s), (0, 0))
    hat = _crop(img, _HAT, s)
    if not (legacy and _fully_opaque(hat)):
        _paste_overlay(canvas, hat, (0, 0))
    scale = max(1, int(scale))
    if scale != s:
        canvas = canvas.resize((8 * scale, 8 * scale), Image.NEAREST)
    return canvas


# ---------------------------------------------------------------- 预览缓存

def _preview_dir() -> Path:
    p = CONFIG.cache_dir / "skin_previews"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_preview(src_path, model: str = "default", scale: int = 8,
                   kind: str = "front") -> str:
    """渲染并缓存预览 PNG，返回本地路径。

    缓存键含皮肤文件内容哈希，皮肤文件改了自动重渲染。
    """
    p = Path(src_path)
    if not p.is_file():
        raise SkinRenderError(f"皮肤文件不存在: {p}")
    kind = (kind or "front").strip().lower()
    if kind not in ("front", "head"):
        raise SkinRenderError(f"不支持的预览类型: {kind}")
    digest = hashlib.sha1(p.read_bytes()).hexdigest()[:20]
    key = f"{digest}-{'slim' if (model or '').lower() == 'slim' else 'default'}-{int(scale)}-{kind}"
    out = _preview_dir() / f"{key}.png"
    if out.is_file():
        return str(out)
    image = render_head(p, scale=scale) if kind == "head" else \
        render_front(p, model=model, scale=scale)
    tmp = out.with_suffix(".tmp")
    image.save(tmp, format="PNG")
    tmp.replace(out)
    return str(out)


def clear_cache():
    p = _preview_dir()
    for f in p.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
