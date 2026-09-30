FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖，利用构建缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制应用代码
COPY app ./app
COPY templates ./templates
COPY static ./static

# SQLite 数据持久化目录
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

# 启动时自动建表并（首次）写入演示数据
CMD ["python", "-m", "app"]
