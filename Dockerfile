FROM osrf/ros:humble-desktop AS rosa-ros2
LABEL authors="Rob Royce"

ENV DEBIAN_FRONTEND=noninteractive
ENV HEADLESS=false
ARG DEVELOPMENT=false

# 安装 Linux 软件包。
RUN apt-get update && apt-get install -y \
    ros-${ROS_DISTRO}-turtlesim \
    locales \
    xvfb \
    python3-pip \
    curl \
    build-essential

# 安装 Rust；构建 tiktoken 时需要它。
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# 开发构建中暂时不启用清理。
# RUN apt-get clean && rm -rf /var/lib/apt/lists/*
# 先升级 pip，再配置 ROS2 shell 环境。
RUN python3 -m pip install --upgrade pip
RUN rosdep update && \
    echo "source /opt/ros/humble/setup.bash" >> /root/.bashrc && \
    echo "alias start_turtlesim='ros2 run turtlesim turtlesim_node'" >> /root/.bashrc

COPY . /app/
WORKDIR /app/

# 根据 ARG 修改 RUN 命令，支持开发模式和普通安装模式。
RUN /bin/bash -c 'if [ "$DEVELOPMENT" = "true" ]; then \
    python3 -m pip install --break-system-packages --ignore-installed --user -e .; \
    else \
    python3 -m pip install --break-system-packages --ignore-installed -U jpl-rosa>=1.0.8; \
    fi'

CMD ["/bin/bash", "-c", "source /opt/ros/humble/setup.bash && \
    if [ \"$HEADLESS\" = \"false\" ]; then \
    ros2 run turtlesim turtlesim_node & \
    else \
    xvfb-run -a -s \"-screen 0 1920x1080x24\" ros2 run turtlesim turtlesim_node & \
    fi && \
    sleep 5 && \
    echo \"已启动 ROS2 TurtleSim。你可以在容器内使用 ROS2 CLI 或自己的 ROSA 脚本连接。\" && \
    /bin/bash"]
