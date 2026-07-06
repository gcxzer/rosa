#!/usr/bin/env bash
# Copyright (c) 2024. Jet Propulsion Laboratory. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# 该脚本用于在 Docker 中启动 ROS2 TurtleSim 环境。

# 检查是否已安装 Docker。
if ! command -v docker &> /dev/null; then
    echo "错误：未安装 Docker。请安装 Docker 后重试。"
    exit 1
fi

# 设置默认 headless 模式。
HEADLESS=${HEADLESS:-false}
DEVELOPMENT=${DEVELOPMENT:-false}

# 根据操作系统启用 X11 转发。
echo "正在启用 X11 转发..."
case "$(uname)" in
    Linux*)
        export DISPLAY=${DISPLAY:-:0}
        xhost +local:docker &>/dev/null || echo "警告：xhost 命令执行失败"
        # 验证 X11 是否正常工作。
        if ! xset q &>/dev/null; then
            echo "错误：X11 转发未正常工作。请检查你的 X11 server。"
            exit 1
        fi
        ;;
    Darwin*)
        # 保留 XQuartz 的 DISPLAY；如果未设置，则默认使用 :0。
        export DISPLAY=${DISPLAY:-:0}
        xhost +local:docker &>/dev/null || true
        
        # 检查 XQuartz 是否正在运行且配置正确。
        if ! pgrep -xq "Xquartz" && ! pgrep -xq "X11"; then
            echo "错误：XQuartz 未运行。请启动 XQuartz 后重试。"
            exit 1
        fi
        
        # 如果网络连接被禁用，则给出提示。
        if ! defaults read org.xquartz.X11 nolisten_tcp 2>/dev/null | grep -q 0; then
            echo "警告：XQuartz 可能不允许网络连接。"
            echo "请在 XQuartz Preferences > Security 中启用 'Allow connections from network clients'"
        fi
        ;;
    MINGW*|CYGWIN*|MSYS*)
        export DISPLAY=host.docker.internal:0
        ;;
    *)
        echo "错误：不支持的操作系统。"
        exit 1
        ;;
esac

# 构建并运行 Docker 容器。
CONTAINER_NAME="rosa-ros2-demo"

# 检测 Apple Silicon 平台。
PLATFORM_ARG=""
if [ "$(uname -m)" = "arm64" ]; then
    PLATFORM_ARG="--platform linux/amd64"
fi

echo "正在构建 $CONTAINER_NAME Docker 镜像..."
docker build $PLATFORM_ARG --build-arg DEVELOPMENT=$DEVELOPMENT -t $CONTAINER_NAME -f Dockerfile . || {
    echo "错误：Docker 构建失败"
    exit 1
}

echo "正在运行 Docker 容器..."
if [ "$(uname)" = "Darwin" ]; then
    # macOS：使用 host.docker.internal 转发 X11。
    docker run -it --rm --init --name $CONTAINER_NAME \
        -e DISPLAY=host.docker.internal:0 \
        -e HEADLESS=$HEADLESS \
        -e DEVELOPMENT=$DEVELOPMENT \
        -v "$PWD/src":/app/src \
        -v "$PWD/tests":/app/tests \
        $CONTAINER_NAME
else
    # Linux/WSL：使用 Unix socket。
    docker run -it --rm --init --name $CONTAINER_NAME \
        -e DISPLAY=$DISPLAY \
        -e HEADLESS=$HEADLESS \
        -e DEVELOPMENT=$DEVELOPMENT \
        -v /tmp/.X11-unix:/tmp/.X11-unix \
        -v "$PWD/src":/app/src \
        -v "$PWD/tests":/app/tests \
        --network host \
        $CONTAINER_NAME
fi

# 禁用 X11 转发。
xhost -local:docker &>/dev/null || true

exit 0
