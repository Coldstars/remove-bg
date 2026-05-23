#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

INPUT_DIR="$PWD/input"
OUTPUT_DIR="$PWD/output"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR"

has_images=false
shopt -s nullglob nocaseglob
for file in "$INPUT_DIR"/*.png "$INPUT_DIR"/*.jpg "$INPUT_DIR"/*.jpeg "$INPUT_DIR"/*.webp; do
  if [ -f "$file" ]; then
    has_images=true
    break
  fi
done
shopt -u nullglob nocaseglob

echo "AI 抠图工具"
echo "----------------------------------------"
echo "输入目录: $INPUT_DIR"
echo "输出目录: $OUTPUT_DIR"
echo

if [ "$has_images" = false ]; then
  echo "input 目录里还没有图片。"
  echo "请把 png/jpg/jpeg/webp 图片放进打开的 input 文件夹。"
  open "$INPUT_DIR"
  echo
  read -r -p "放好图片后按回车开始；直接关闭窗口可取消..."
fi

echo
echo "开始处理。第一次加载大模型会比较慢，请耐心等待..."
echo

./run.sh

echo
echo "处理完成，正在打开 output 文件夹。"
open "$OUTPUT_DIR"
echo
read -r -p "按回车关闭窗口..."
