"""Human-readable metadata for the keys found in War Thunder's ``config.blk``.

Two rules keep this honest:

1. A key only gets a curated dropdown when the candidate values are ones the
   game is actually known to write.  Every string editor stays **editable**, so
   a user can always type a raw value instead of being forced into a guess.
2. Presets only ever touch keys whose meaning is unambiguous (the master
   ``graphicsQuality`` setting plus explicit booleans/numbers).  The game then
   derives the rest of its settings itself.

Keys that are not described here still appear in the editor under 其它参数 with
a type-appropriate control, so nothing in the file is hidden or uneditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "KeySpec",
    "BLOCK_ORDER",
    "BLOCK_LABELS",
    "BLOCK_HELP",
    "SCHEMA",
    "ADDON_TOGGLES",
    "QUALITY_PRESETS",
    "spec_for",
    "block_label",
]


@dataclass(frozen=True)
class KeySpec:
    label: str
    help: str = ""
    enum: tuple[tuple[str, str], ...] = ()
    """``((value, display_label), ...)`` curated suggestions for string keys."""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    decimals: int = 0
    advanced: bool = False
    """Hidden behind the 高级 switch - rarely worth touching."""
    dangerous: bool = False
    """Shown with a caution marker; changing it can break launching."""

    @property
    def choices(self) -> tuple[tuple[str, str], ...]:
        return self.enum


# Quality vocabulary the game writes; offered as *editable* suggestions.
_QUALITY = (
    ("ultralow", "最低"),
    ("low", "低"),
    ("medium", "中"),
    ("high", "高"),
    ("ultrahigh", "极高"),
    ("movie", "电影"),
)
_SHADOW_QUALITY = _QUALITY
_TEX_QUALITY = (
    ("low", "低"),
    ("medium", "中"),
    ("high", "高"),
    ("ultrahigh", "极高"),
)
_FX_QUALITY = (
    ("ultralow", "最低"),
    ("low", "低"),
    ("medium", "中"),
    ("high", "高"),
)
_WATER_QUALITY = (
    ("ultralow", "最低"),
    ("low", "低"),
    ("medium", "中"),
    ("high", "高"),
)
_GI_QUALITY = (
    ("off", "关闭"),
    ("low", "低"),
    ("medium", "中"),
    ("high", "高"),
)
_GRAPHICS_QUALITY = (
    ("ultralow", "最低画质"),
    ("low", "低画质"),
    ("medium", "中画质"),
    ("high", "高画质"),
    ("movie", "电影级"),
)
_MODE = (
    ("fullscreen", "独占全屏"),
    ("windowed", "窗口化"),
)
_DRIVER = (
    ("auto", "自动"),
    ("dx11", "DirectX 11"),
    ("dx12", "DirectX 12"),
    ("vulkan", "Vulkan"),
)
_FONT_SIZE = (
    ("big", "大"),
    ("normal", "中"),
    ("small", "小"),
)
_ON_OFF = (
    ("off", "关闭"),
)
_RT_QUALITY = (
    ("off", "关闭"),
    ("low", "低"),
    ("medium", "中"),
    ("high", "高"),
)
_RES_SCALE = (
    ("native", "原生（不缩放）"),
)


# --------------------------------------------------------------------------- #
#  Blocks
# --------------------------------------------------------------------------- #
BLOCK_LABELS: dict[str, str] = {
    "": "顶层设置",
    "video": "画面与显示",
    "graphics": "图形细节",
    "render": "渲染效果",
    "sound": "声音",
    "gameplay": "玩法",
    "launcher": "启动器",
    "download": "下载",
    "debug": "调试与截图",
    "yunetwork": "网络",
}

BLOCK_HELP: dict[str, str] = {
    "": "游戏启动时读取的全局设置，包括语言与画质主预设。",
    "video": "分辨率、显示模式、垂直同步与帧率上限。",
    "graphics": "各项画质细节。通常直接使用主预设 graphicsQuality 即可。",
    "render": "环境光遮蔽、屏幕空间反射与阴影。",
    "sound": "声音引擎与扬声器模式。",
    "gameplay": "玩法相关，例如 VR。",
    "launcher": "官方启动器的行为（托盘、后台更新、开机自启）。",
    "download": "启动器下载与做种限速。",
    "debug": "日志与截图相关，普通玩家一般不需要改动。",
    "yunetwork": "网络线路设置。",
}

BLOCK_ORDER: tuple[str, ...] = (
    "",
    "video",
    "graphics",
    "render",
    "sound",
    "gameplay",
    "launcher",
    "download",
    "debug",
    "yunetwork",
)


def _spec(label: str, help_text: str = "", **kwargs) -> KeySpec:
    return KeySpec(label=label, help=help_text, **kwargs)


# --------------------------------------------------------------------------- #
#  Per-block key metadata
# --------------------------------------------------------------------------- #
SCHEMA: dict[str, dict[str, KeySpec]] = {
    "": {
        "language": _spec("界面语言", "游戏客户端使用的语言。", enum=(
            ("Chinese", "简体中文"), ("English", "English"), ("Russian", "Русский"),
            ("German", "Deutsch"), ("French", "Français"), ("Japanese", "日本語"),
            ("Korean", "한국어"), ("Polish", "Polski"), ("Czech", "Čeština"),
            ("Turkish", "Türkçe"), ("Portuguese", "Português"), ("Spanish", "Español"),
            ("Italian", "Italiano"), ("Hungarian", "Magyar"), ("Ukrainian", "Українська"),
        )),
        "graphicsQuality": _spec(
            "画质主预设",
            "游戏会根据这一项自动派生大多数图形选项，是最省事也最安全的画质开关。",
            enum=_GRAPHICS_QUALITY,
        ),
        "use_eac": _spec(
            "启用 EasyAntiCheat",
            "在线对战必需。关闭后只能进入部分模式，且可能无法匹配。",
            dangerous=True,
        ),
        "forcedLauncher": _spec("强制使用启动器", "大于 0 时游戏总是先拉起官方启动器。", minimum=0, maximum=1),
        "releaseChannel": _spec("更新通道", "留空表示正式服。除非你明确知道填什么，否则不要改。", advanced=True),
        "use_release_candidate": _spec("使用候选版本", "抢先体验测试版本，可能出现不稳定。", advanced=True),
        "rdseed": _spec("随机种子", "游戏内部使用，请勿修改。", advanced=True, dangerous=True),
        "distr": _spec("分发标识", "由安装程序写入，请勿修改。", advanced=True, dangerous=True),
        "firstRun": _spec("首次运行标记", "由游戏自己维护。", advanced=True),
        "firstYup": _spec("首次更新标记", "由启动器自己维护。", advanced=True),
        "firstDownload": _spec("首次下载标记", "由启动器自己维护。", advanced=True),
        "firstDownloaded": _spec("首次下载完成标记", "由启动器自己维护。", advanced=True),
        "firstGameShow": _spec("首次展示标记", "由游戏自己维护。", advanced=True),
        "firstGameRun": _spec("首次启动标记", "由游戏自己维护。", advanced=True),
    },
    "video": {
        "mode": _spec("显示模式", "独占全屏性能最好；窗口化便于多任务与录屏。", enum=_MODE),
        "resolution": _spec(
            "分辨率",
            "留空或 auto 表示使用桌面分辨率；也可以写成 1920x1080 这样的固定值。",
        ),
        "vsync": _spec("垂直同步", "消除画面撕裂，但会略微增加输入延迟。竞技向建议关闭。"),
        "fpsLimit": _spec("帧率上限", "0 表示不限制。设为显示器刷新率附近通常最稳定。", minimum=0, maximum=500, step=5),
        "menuFpsLimit": _spec("菜单帧率上限", "0 表示不限制。限制它可以降低待机功耗与发热。", minimum=0, maximum=500, step=5),
        "driver": _spec("图形接口", "auto 由游戏决定。切换接口可能影响稳定性与性能。", enum=_DRIVER),
        "antialiasing_mode": _spec(
            "抗锯齿模式",
            "写入游戏支持的模式名（如 off）。不同版本可用值可能不同，请谨慎修改。",
            enum=(("off", "关闭"),),
        ),
        "antialiasing_upscaling": _spec(
            "超分辨率 / 缩放",
            "native 表示原生渲染。其它取值（如 FSR / DLSS / XeSS / TSR）取决于你的显卡与游戏版本。",
            enum=_RES_SCALE,
        ),
        "antialiasing_fgc": _spec("帧生成", "部分版本用于帧生成等级，0 表示关闭。", minimum=0, maximum=4, advanced=True),
        "antialiasing_sharpening": _spec("锐化强度", "数值越高画面越锐利，也越容易出现噪点。", minimum=0, maximum=100),
        "windowed": _spec("窗口化标记", "与显示模式配套，一般无需单独修改。", advanced=True),
        "compatibilityMode": _spec(
            "兼容模式",
            "更保守的渲染路径，遇到花屏或闪退时可以尝试开启。",
        ),
        "enableHdr": _spec("HDR", "需要显示器与系统同时支持 HDR。"),
        "latency": _spec("低延迟模式", "0 为关闭。数值含义随显卡驱动而异。", minimum=0, maximum=4),
        "perfMetrics": _spec("性能统计叠加", "在屏幕上显示帧率与帧时间等信息。", minimum=0, maximum=3),
        "fonts": _spec("界面字号", "影响游戏内文字的大小。", enum=_FONT_SIZE),
        "vreye": _spec("VR 眼别", "仅 VR 相关。", advanced=True),
        "vrStreamerMode": _spec("VR 串流模式", "仅 VR 相关。", advanced=True),
        "rayReconstruction": _spec("光线重建", "需要支持该技术的显卡。", advanced=True),
    },
    "graphics": {
        "texquality": _spec("纹理质量", "显存占用最大的选项；显存不足时优先调低。", enum=_TEX_QUALITY),
        "shadowQuality": _spec("阴影质量", "对帧率影响很大。", enum=_SHADOW_QUALITY),
        "waterQuality": _spec("水面质量", "海战与两栖作战场景影响明显。", enum=_WATER_QUALITY),
        "waterEffectsQuality": _spec("水面特效质量", "浪花、泡沫等细节。", enum=_WATER_QUALITY),
        "fxQuality": _spec("特效质量", "爆炸、烟雾等粒子效果，对帧率影响较大。", enum=_FX_QUALITY),
        "giQuality": _spec("全局光照质量", "间接光照，影响画面整体氛围。", enum=_GI_QUALITY),
        "ssaa": _spec(
            "超级采样 (SSAA)",
            "以更高分辨率渲染再缩放，画质提升明显但性能开销很大。",
            minimum=1.0, maximum=4.0, step=0.25, decimals=2,
        ),
        "anisotropy": _spec("各向异性过滤", "改善远处地面的纹理清晰度，开销很小。", minimum=1, maximum=16, step=1),
        "rendinstDistMul": _spec(
            "渲染物体距离倍率",
            "控制建筑、树木等静态物体的可见距离。对帧率与画面公平性都有影响。",
            minimum=0.0, maximum=2.0, step=0.05, decimals=2,
        ),
        "grassRadiusMul": _spec(
            "草地范围倍率",
            "降低它可以明显提升帧率，也会减少掩体视觉遮挡。",
            minimum=0.0, maximum=2.0, step=0.05, decimals=2,
        ),
        "fxDistortionStrength": _spec("特效扭曲强度", "热浪等视觉扭曲强度，0 为关闭。", minimum=0.0, maximum=1.0, step=0.05, decimals=2),
        "motionBlurStrength": _spec("动态模糊强度", "0 为关闭。关闭通常更利于观察目标。", minimum=0, maximum=100),
        "motionBlurCancelCamera": _spec("转动视角时取消动态模糊", "减轻转动视角时的模糊感。"),
        "lenseFlares": _spec("镜头光晕", "太阳造成的镜头光斑。"),
        "contactShadowsQuality": _spec("接触阴影质量", "物体与地面接触处的阴影细节。", minimum=0, maximum=3),
        "displacementQuality": _spec("位移贴图质量", "地面起伏细节。", minimum=0, maximum=3),
        "tireTracksQuality": _spec("车辙质量", "车辆留下的履带与轮胎痕迹。", minimum=0, maximum=3),
        "physicsQuality": _spec("物理质量", "破碎与刚体模拟的精细程度。", minimum=0, maximum=3),
        "landquality": _spec("地形质量", "地表网格精细程度。", minimum=0, maximum=3),
        "cloudsQuality": _spec("云层质量", "对空战视野与帧率都有影响。", minimum=0, maximum=4),
        "skyQuality": _spec("天空质量", "天空盒与大气散射精度。", minimum=0, maximum=4),
        "volfogQuality": _spec("体积雾质量", "低空雾效，关闭可提升帧率。", enum=_QUALITY),
        "bloomQuality": _spec("泛光质量", "高光溢出效果。", minimum=0, maximum=4),
        "mirrorQuality": _spec("镜面反射质量", "后视镜与水面的反射精度。", minimum=0, maximum=3),
        "panoramaResolution": _spec("全景反射分辨率", "环境反射贴图分辨率。", minimum=256, maximum=4096, step=256),
        "lastClipSize": _spec("贴花剪裁尺寸", "弹痕与涂装贴花的保存尺寸，越大越清晰也越吃显存。", minimum=256, maximum=8192, step=256, advanced=True),
        "backgroundScale": _spec("背景缩放", "远景渲染比例。", minimum=0.5, maximum=2.0, step=0.05, decimals=2, advanced=True),
        "advancedShore": _spec("高级海岸线", "更精细的海岸过渡。", advanced=True),
        "riGpuObjects": _spec("GPU 物体实例化", "把静态物体交给 GPU 绘制。", advanced=True),
        "bvhRiGenRange": _spec("BVH 生成范围", "光线追踪相关的可见范围。", minimum=0, maximum=10000, step=100, advanced=True),
        "enableBVH": _spec("启用 BVH", "光线追踪加速结构。", advanced=True),
        "bvhMode": _spec("BVH 模式", "光线追踪相关。", enum=(("off", "关闭"),), advanced=True),
        "enableRTSM": _spec("启用光追阴影", "需要支持光追的显卡。", enum=(("off", "关闭"),), advanced=True),
        "RTSMQuality": _spec("光追阴影质量", "需要支持光追的显卡。", enum=_RT_QUALITY, advanced=True),
        "RTAOQuality": _spec("光追环境光遮蔽质量", "需要支持光追的显卡。", enum=_RT_QUALITY, advanced=True),
        "RTRQuality": _spec("光追反射质量", "需要支持光追的显卡。", enum=_RT_QUALITY, advanced=True),
        "RTRRes": _spec("光追反射分辨率", "half 为半分辨率。", enum=(("half", "半分辨率"), ("full", "全分辨率")), advanced=True),
        "RTRWater": _spec("水面光追反射", "需要支持光追的显卡。", advanced=True),
        "RTRWaterRes": _spec("水面光追分辨率", "half 为半分辨率。", enum=(("half", "半分辨率"), ("full", "全分辨率")), advanced=True),
        "RTRTranslucent": _spec("半透明光追反射", "玻璃等材质的光追反射。", enum=(("off", "关闭"), ("on", "开启")), advanced=True),
        "RTDecals": _spec("光追贴花", "需要支持光追的显卡。", advanced=True),
        "PTGIQuality": _spec("路径追踪全局光照", "极高开销，仅供高端显卡尝试。", enum=_RT_QUALITY, advanced=True),
    },
    "render": {
        "shadows": _spec("阴影", "总开关。关闭可显著提升帧率，但画面明显变平。"),
        "ssaoQuality": _spec("环境光遮蔽质量", "物体缝隙处的接触阴影。", minimum=0, maximum=3),
        "ssrQuality": _spec("屏幕空间反射质量", "水面与光滑表面的反射。", minimum=0, maximum=3),
        "selfReflection": _spec("自身反射", "载具对自身的反射。", advanced=True),
    },
    "sound": {
        "fmod_sound_enable": _spec("启用游戏音效", "关闭后游戏将没有声音。", dangerous=True),
        "speakerMode": _spec(
            "扬声器模式",
            "auto 会跟随系统设置。耳机模式下定位更准。",
            enum=(("auto", "自动"), ("stereo", "立体声"), ("headphones", "耳机"), ("surround", "环绕声")),
        ),
    },
    "gameplay": {
        "enableVR": _spec("启用 VR", "需要 VR 头显与对应运行环境。"),
    },
    "launcher": {
        "bg_update": _spec("后台更新", "启动器在后台检查与下载更新。"),
        "hide_to_tray_option": _spec("允许隐藏到托盘", "启用启动器的托盘选项。"),
        "bg_tray": _spec("后台驻留托盘", "启动器关闭后仍驻留系统托盘。"),
        "startup_with_windows": _spec(
            "随 Windows 启动",
            "让官方启动器随系统启动。注意：这是启动器的设置，与本工具的“开机自启”是两回事。",
        ),
    },
    "download": {
        "dnl_limit": _spec("启用下载限速", "开启后按 dnl_speed_rate 限制下载速度。"),
        "dnl_speed_rate": _spec("下载限速值", "单位为 KB/s。", minimum=0, maximum=1_000_000, step=100),
        "upl_limit": _spec("启用上传限速", "开启后按 upl_speed_rate 限制上传速度。"),
        "upl_speed_rate": _spec("上传限速值", "单位为 KB/s。", minimum=0, maximum=1_000_000, step=100),
        "seeding_on": _spec("做种", "下载完成后继续向其他玩家上传。"),
        "DHT": _spec("启用 DHT", "去中心化节点发现，有助于提速。"),
        "peer_exchange": _spec("节点交换", "与其他客户端交换可用节点。"),
        "UTP2": _spec("启用 uTP2", "一种拥塞控制的传输协议。", advanced=True),
    },
    "debug": {
        "netLogerr": _spec("记录网络错误", "网络异常时写入日志，排查问题时有用。"),
        "screenshotAsJpeg": _spec("截图保存为 JPEG", "关闭则保存为 PNG（体积更大、无损）。"),
        "screenshotHiRes": _spec("高分辨率截图", "以更高分辨率保存截图，占用更大。"),
    },
    "yunetwork": {
        "curCircuit": _spec("当前线路", "启动器使用的下载线路。", advanced=True),
    },
}


# --------------------------------------------------------------------------- #
#  Presets
# --------------------------------------------------------------------------- #
QUALITY_PRESETS: tuple[tuple[str, str, str], ...] = (
    ("ultralow", "最低画质", "帧率优先：纹理与阴影最低，适合低配机器。"),
    ("low", "低画质", "在帧率与画面之间取得平衡。"),
    ("medium", "中等画质", "默认取向，大多数机器可用。"),
    ("high", "高画质", "画面优先，需要较强的显卡。"),
    ("movie", "电影画质", "最高画质，仅供高端配置。"),
)

# Extra, individually opt-in tweaks with unambiguous semantics.
# ``(block, key, type_code, title, help, value)``
ADDON_TOGGLES: tuple[tuple[str, str, str, str, str, object], ...] = (
    ("video", "vsync", "b", "关闭垂直同步", "降低输入延迟，可能产生画面撕裂。", False),
    ("video", "fpsLimit", "i", "把帧率上限设为 120", "0 表示不限制；限制帧率可降低发热与功耗。", 120),
    ("graphics", "motionBlurStrength", "i", "关闭动态模糊", "转动视角时画面更清晰，便于索敌。", 0),
    ("graphics", "grassRadiusMul", "r", "降低草地范围到 0.1", "明显提升帧率，但地面掩体的视觉遮挡会减少。", 0.1),
    ("graphics", "rendinstDistMul", "r", "降低渲染距离到 0.5", "提升帧率，远处建筑会更晚出现。", 0.5),
    ("video", "perfMetrics", "i", "显示性能统计", "在屏幕上显示帧率与帧时间。", 1),
)


def spec_for(block: str, key: str) -> KeySpec | None:
    return SCHEMA.get(block, {}).get(key)


def block_label(block: str) -> str:
    return BLOCK_LABELS.get(block, block or "顶层设置")
