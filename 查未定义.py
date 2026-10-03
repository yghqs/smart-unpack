#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态查「引用了但没定义」的名字（标准库 symtable，不跑代码）。
拆分后必须跑一遍：不跑就得靠一个个 NameError 去猜，那是笨办法。"""
import builtins
import io
import symtable
import sys


# 解释器注入的模块级名字：静态分析看不见它们，但运行期一定有 ⇒ 不算「未定义」
注入的 = {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
        "__builtins__", "__debug__", "__cached__"}


def 查(路径):
    源码 = io.open(路径, encoding="utf-8").read()
    表 = symtable.symtable(源码, 路径, "exec")

    模块级 = set()
    for s in 表.get_symbols():
        if s.is_assigned() or s.is_imported() or s.is_namespace() or s.is_parameter():
            模块级.add(s.get_name())

    坏 = []
    影子 = []

    def 走(表):
        for s in 表.get_symbols():
            名 = s.get_name()
            if not s.is_referenced():
                continue
            if s.is_assigned() or s.is_parameter():
                continue
            if 表.get_type() == "module" or s.is_global():
                if 名 not in 模块级 and 名 not in 注入的 and not hasattr(builtins, 名):
                    坏.append((名, 表.get_name()))
        for 子 in 表.get_children():
            走(子)

    def 走影子(表):
        """⚠️ 补的第二把闸：**函数里赋值了同名模块级变量、却没声明 `global`**。
        那时它在函数里是**局部变量**，`symtable` 看它"有定义"，上面那条查不出来 ——
        但读的时候会 `UnboundLocalError`，而且往往**整条主路径全崩**
        （2026-10-04 实测：给 `再解一层` 加三行临时清全局变量的代码，漏了 `global`，
         凡有内层压缩包的包一律崩）。
        判据：同名既是**模块级**、又在函数里**被赋值**且**没声明 global** ⇒ 报疑似。
        ⚠️ **门槛**：短名（≤2 字符，如 `e`／`f`／`i`）一律跳过 ——
        实测 `e` 会被误报（它既是模块级的异常变量 `except X as e`、又在 `main` 里被赋值），
        **阴对照当场抓到的噪音**；而真正会漏 global 的，都是**名字较长、有意起得有含义**的模块级变量
        （比如 `全局计数器` 这种），不会是 `e`／`i` 这种随手起的名。
        宁可漏报短名，也不要让闸天天狼来了。"""
        for 子 in 表.get_children():
            for s in 子.get_symbols():
                名 = s.get_name()
                if len(名) <= 2:
                    continue          # 门槛：短名跳过，见上
                if 名 in 模块级 and s.is_assigned() and not s.is_parameter() and not s.is_global():
                    影子.append((名, 子.get_name()))
            走影子(子)

    走(表)
    走影子(表)
    return sorted(set(坏)), sorted(set(影子))


失败 = False
for p in sys.argv[1:]:
    坏, 影子 = 查(p)
    if 坏:
        失败 = True
        print("== %s ==" % p)
        for 名, 在 in 坏:
            print("   未定义：%-16s 出现在 %s" % (名, 在))
    else:
        print("== %s == 干净" % p)
    if 影子:
        print("   ⚠️ 疑似**漏 global**（同名既是模块级、又在函数里被赋值）—— 读它时会 UnboundLocalError：")
        for 名, 在 in 影子:
            print("       %-16s 在 %s 里被赋值" % (名, 在))

sys.exit(1 if 失败 else 0)
