#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全面TTS测试和修复脚本
测试多种TTS解决方案，找出可用的方案
"""

import os
import sys
import time
import asyncio
import edge_tts
import subprocess

print("=" * 70)
print("TTS 综合测试脚本")
print("=" * 70)

# 测试文本
TEST_TEXT = "你好，我在测试TTS功能"

# 输出目录
OUTPUT_DIR = "E:/PycharmDemo/digital-human-web/web/media/audio"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# =============================================================================
# 测试1: Edge TTS (当前方案)
# =============================================================================
print("\n[测试1] Edge TTS (微软)")
print("-" * 70)

async def test_edge_tts():
    output_file = os.path.join(OUTPUT_DIR, "test_edge_tts.mp3")

    try:
        print(f"文本: {TEST_TEXT}")

        # 列出可用语音
        print("正在获取可用语音列表...")
        voices = await edge_tts.list_voices()
        print(f"可用语音数量: {len(voices)}")

        # 查找中文语音
        zh_voices = [v for v in voices if 'zh-CN' in v['ShortName']]
        print(f"中文语音: {zh_voices[:3] if zh_voices else '无'}")

        # 尝试生成TTS
        print("尝试生成TTS...")
        start = time.time()

        communicate = edge_tts.Communicate(TEST_TEXT, "zh-CN-XiaoxiaoNeural")
        await communicate.save(output_file)

        elapsed = time.time() - start

        # 检查文件
        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            if size > 0:
                print(f"✅ 成功! 文件: {output_file}")
                print(f"   大小: {size} bytes")
                print(f"   耗时: {elapsed:.2f}s")
                return True
            else:
                print(f"❌ 失败: 文件为空")
                os.remove(output_file)
                return False
        else:
            print(f"❌ 失败: 文件未生成")
            return False

    except Exception as e:
        print(f"❌ 异常: {str(e)[:100]}")
        return False

# 运行测试1
result1 = asyncio.run(test_edge_tts())

# =============================================================================
# 测试2: 检查网络连接
# =============================================================================
print("\n[测试2] 网络连接检查")
print("-" * 70)

try:
    # 测试ping微软语音服务
    result = subprocess.run(
        ["ping", "-n", "3", "speech.platform.bing.com"],
        capture_output=True,
        text=True,
        timeout=10
    )
    if result.returncode == 0:
        print("✅ 可以ping通 speech.platform.bing.com")
    else:
        print("❌ 无法ping通 speech.platform.bing.com")
except Exception as e:
    print(f"❌ Ping测试失败: {e}")

# 测试DNS解析
try:
    import socket
    ip = socket.gethostbyname("speech.platform.bing.com")
    print(f"✅ DNS解析成功: speech.platform.bing.com -> {ip}")
except Exception as e:
    print(f"❌ DNS解析失败: {e}")

# =============================================================================
# 测试3: 代理设置测试
# =============================================================================
print("\n[测试3] 代理设置检查")
print("-" * 70)

# 检查环境变量中的代理设置
proxy_vars = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
found_proxy = False
for var in proxy_vars:
    value = os.getenv(var)
    if value:
        print(f"✅ 找到代理设置: {var}={value}")
        found_proxy = True

if not found_proxy:
    print("ℹ️  未设置代理 (可能不需要，或者需要配置)")

# =============================================================================
# 测试4: 其他TTS方案
# =============================================================================
print("\n[测试4] 检查系统TTS")
print("-" * 70)

# 检查Windows SAPI
try:
    # 使用PowerShell检查SAPI
    ps_script = '''
Add-Type -TypeDefinition @"
using System;
using System.Speech.Synthesis;
public class TestTTS {
    public static void Main() {
        SpeechSynthesizer synth = new SpeechSynthesizer();
        Console.WriteLine("SAPI可用语音:");
        foreach (VoiceInfo v in synth.GetInstalledVoices()) {
            Console.WriteLine($"  {v.Name} - {v.Culture}");
        }
    }
}
"@
[TestTTS]::Main()
'''

    result = subprocess.run(
        ["powershell", "-Command", ps_script],
        capture_output=True,
        text=True,
        timeout=10
    )

    if result.returncode == 0:
        print("✅ Windows SAPI 可用")
        print("可用语音:")
        for line in result.stdout.split('\n')[:10]:
            if line.strip():
                print(f"  {line.strip()}")
    else:
        print("❌ Windows SAPI 不可用")
        print(f"错误: {result.stderr[:100]}")

except Exception as e:
    print(f"❌ SAPI测试失败: {e}")

# =============================================================================
# 总结和建议
# =============================================================================
print("\n" + "=" * 70)
print("测试总结")
print("=" * 70)

if result1:
    print("✅ Edge TTS 工作正常 - 当前方案可用")
else:
    print("❌ Edge TTS 不工作 - 需要修复")

print("\n建议的解决方案:")
print("1. 如果Edge TTS工作 - 继续使用当前方案")
print("2. 如果Edge TTS不工作:")
print("   a) 配置代理 (如果有)")
print("   b) 换用Windows SAPI")
print("   c) 换用国内TTS服务 (百度/阿里云)")
print("=" * 70)
