# 构建阶段
FROM node:20-alpine AS build
WORKDIR /app
# P2-E10：lock 已入库，固定 npm ci 可复现构建——去掉 `|| npm install` 回退（回退会按
# package.json 重新解析导致 lock 漂移，失去「可复现构建」保障）；lock 过期时构建暴露失败，
# 强制先同步 lock（与 D13 后端依赖 lock 同一精神）
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# 运行阶段
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
