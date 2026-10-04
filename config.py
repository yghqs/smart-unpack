#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可调配置 —— **留给使用者的口子**。

取值优先级（从高到低）：

    命令行参数  >  配置文件  >  这里写的默认值

配置文件（可选）放在**程序旁边**，叫 `smart-unpack.ini`，长这样：

    # 注释必须**独占一行**（`configparser` 不认行尾注释 —— 写在值后面会被当成值的一部分）
    [smart-unpack]
    上限GB = 5
    输出后缀 = .zip
    解压层数 = 0
    上限层数 = 6
    停滞秒 = 120
    条目超时秒 = 0
    集中模式 = false
    递归子目录 = true

完整带注释的样例见随包的 `smart-unpack.ini.example`。
没有这个文件也照常跑（全用默认值）。
"""
import io
import os
import sys

# ------------------------------------------------------------
# 默认值（这是唯一正本；命令行与配置文件都只是覆盖它）
# ------------------------------------------------------------
默认 = {
    "上限GB": 5.0,          # **盘上占用**多少 GB 就停（＝现在还占着的量；中间层收走会还回来）
    "输出后缀": ".zip",      # 输出目录名按此后缀归一化
    "仅处理后缀": "",        # 只处理这些后缀（逗号分隔）；空 = 全都处理
    "递归子目录": True,       # 连子目录一起找
    "解压层数": 0,           # 0 = 自动（解出来还是压缩包就接着解）
    "上限层数": 6,           # 自动模式的安全上限
    "停滞秒": 120,           # 连续多少秒没有新进展 ⇒ 判卡死
    "条目超时秒": 0,         # 单条目总耗时上限；0 = 不限（慢 ≠ 坏）
    "心跳秒": 10,            # 大条目每多久报一次「已写 X/Y」
    "条目记账秒": 2.0,       # 单条目超过多少秒单独记一行
    "整包明细秒": 10.0,      # 整包合计超过多少秒才多打一行
    "集中模式": False,       # False = 每包一目录；True = 全并到一个文件夹
    # 「只解压缩包」模式的**兜底名单**（逗号分隔）：这些后缀**只当交付物、不拆**。
    # 主判据是**内容特征**（包里带 [Content_Types].xml / META-INF/MANIFEST.MF / project.json …），
    # 名单只在内容判不出来时补一刀。命令行 `--全部拆` 可整个无视它。
    # ⚠️ **默认空**（2026-10-04 用户裁「后缀名单的默认值改空可以」）——
    #    名单按后缀判，会把「名字起得像交付物、内容其实是普通分享包」的**误杀**
    #    （实测一次运行里 14 个正常分享包被整条挡掉）。默认空 ⇒ 行为**完全跟内容判据走**：
    #    真 docx/jar 照样挡得住（包里带 [Content_Types].xml / META-INF/MANIFEST.MF…），
    #    名字骗人但内容是普通 zip 的 ⇒ **照拆**。想加自己填，逗号分隔。
    "不拆后缀": "",
}

真值 = {"1", "true", "yes", "y", "on", "是", "真"}
假值 = {"0", "false", "no", "n", "off", "否", "假"}


def 程序目录():
    return os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))


def _按类型(原值, 文本):
    """照默认值那边的类型，把配置文件里的字符串转回来。"""
    if isinstance(原值, bool):
        低 = 文本.strip().lower()
        if 低 in 真值:
            return True
        if 低 in 假值:
            return False
        raise ValueError("要 true/false，给了 %r" % 文本)
    if isinstance(原值, int) and not isinstance(原值, bool):
        return int(float(文本))
    if isinstance(原值, float):
        return float(文本)
    return 文本


def 载入(目录=None):
    """返回一份配置：默认值 + 配置文件里的覆盖。**读不到配置文件不算错。**"""
    值 = dict(默认)
    路径 = os.path.join(目录 or 程序目录(), "smart-unpack.ini")
    if not os.path.isfile(路径):
        return 值, None
    try:
        import configparser
        解析 = configparser.ConfigParser()
        # ⚠️ configparser 默认把**键名转小写**（optionxform）⇒「上限GB」会变成「上限gb」，
        #    跟默认表里的键对不上、被静默跳过。必须关掉这个转换。
        解析.optionxform = str
        解析.read(路径, encoding="utf-8")
        段 = "smart-unpack" if 解析.has_section("smart-unpack") else 解析.sections()[0] if 解析.sections() else None
        if 段:
            不认识的 = []
            for 键, 文本 in 解析.items(段):
                if 键 in 默认:
                    try:
                        值[键] = _按类型(默认[键], 文本)
                    except ValueError as e:
                        raise ValueError("配置项 %s：%s" % (键, e))
                else:
                    # ⚠️ 不认识的键**必须报错**，不能静默忽略 ——
                    #    拼错一个键（如 `上限gb`）却照旧用默认值，是最难查的那类坑。
                    不认识的.append(键)
            if 不认识的:
                raise ValueError("不认识的配置项：%s（认得的项见 config.py 里的「默认」表）"
                                 % "、".join(不认识的))
    except Exception as e:
        raise SystemExit("配置文件读不动（%s）：%s" % (路径, e))
    return 值, 路径


def 仅处理后缀列表(值):
    """配置文件里 `仅处理后缀` 是逗号分隔的字符串；这里转成列表。"""
    if isinstance(值, (list, tuple)):
        return [str(x).strip() for x in 值 if str(x).strip()]
    return [x.strip() for x in str(值 or "").replace("，", ",").split(",") if x.strip()]
