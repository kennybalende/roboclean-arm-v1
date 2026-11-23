FROM node:22-alpine
WORKDIR /app
COPY package.json ./
COPY server ./server
COPY web ./web
ENV PORT=3000 DB_PATH=/app/data/roboclean.db
VOLUME /app/data
EXPOSE 3000
CMD ["node", "server/server.js"]
