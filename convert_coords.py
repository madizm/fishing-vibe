#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WGS84/CGCS2000 → GCJ-02 坐标转换
腾讯地图（小程序 map 组件）使用 GCJ-02，天地图数据使用 CGCS2000（≈WGS84）
本脚本将 fishing-spots.json 的坐标转换后输出新文件
"""

import json
import math
import sys
import os

# ─── GCJ-02 转换算法（标准形式）───────────────────────────────────────

A = 6378245.0           # 长半轴
EE = 0.00669342162296594323  # 偏心率平方


def _transform_lat(lng, lat):
    """计算纬度偏移量（内部函数）"""
    ret = (-100.0 + 2.0 * lng + 3.0 * lat
           + 0.2 * lat * lat + 0.1 * lng * lat
           + 0.2 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi)
           + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi)
           + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi)
           + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng, lat):
    """计算经度偏移量（内部函数）"""
    ret = (300.0 + lng + 2.0 * lat + 0.1 * lng * lng
           + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi)
           + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi)
           + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi)
           + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng, lat):
    """
    WGS84/CGCS2000 → GCJ-02
    在中国大陆范围内有约 100~700m 非线性偏移
    返回值：(lng_gcj, lat_gcj)
    """
    # 中国境外不偏移
    if lng < 72.004 or lng > 137.8347 or lat < 0.8293 or lat > 55.8271:
        return lng, lat

    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)

    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1.0 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)

    d_lat = (d_lat * 180.0) / ((A * (1.0 - EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (A / sqrt_magic * math.cos(rad_lat) * math.pi)

    return lng + d_lng, lat + d_lat


# ─── 批量转换 ──────────────────────────────────────────────────────────

def convert_geojson(input_path, output_path):
    """
    读取 WGS84 GeoJSON，转换为 GCJ-02 后输出。
    output_path 以 .json 结尾 → 输出 JSON 文件
    output_path 以 .js 结尾   → 输出 CommonJS 模块（小程序 require 友好）
    """
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total = len(data.get('features', []))
    converted = 0

    for feature in data['features']:
        coords = feature.get('geometry', {}).get('coordinates')
        if not coords or len(coords) < 2:
            continue
        lng, lat = coords[0], coords[1]
        new_lng, new_lat = wgs84_to_gcj02(lng, lat)
        feature['geometry']['coordinates'] = [new_lng, new_lat]
        converted += 1

    is_js = output_path.endswith('.js')
    with open(output_path, 'w', encoding='utf-8') as f:
        if is_js:
            f.write('module.exports = ')
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write(';\n')
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)

    kind = 'JS 模块' if is_js else 'JSON'
    print(f'转换完成：{converted}/{total} 个钓点 → {kind}')
    print(f'   输入：{input_path}')
    print(f'   输出：{output_path}')

    # 打印一个示例偏移量
    if data['features']:
        sample = data['features'][0]
        g_lng, g_lat = sample['geometry']['coordinates']
        print(f'\n示例（第1个钓点 {sample["properties"]["place_name"]}）：')
        print(f'   GCJ-02: [{g_lng:.6f}, {g_lat:.6f}]')


if __name__ == '__main__':
    base = os.path.dirname(os.path.abspath(__file__))
    # web 版原始数据（WGS84/CGCS2000）
    inp = os.path.join(base, 'web', 'fishing-spots.json')
    # 小程序直接 require 的 JS 模块（GCJ-02）
    out = os.path.join(base, 'miniprogram', 'utils', 'fishing-spots-data.js')
    convert_geojson(inp, out)
