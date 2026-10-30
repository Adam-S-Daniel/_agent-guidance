"use strict";

const http = require("node:http");
const { greeting } = require("./util");

const PORT = Number(process.env.PORT || 8080);

const server = http.createServer((req, res) => {
  if (req.url === "/healthz") {
    res.writeHead(204);
    res.end();
    return;
  }
  res.writeHead(200, { "content-type": "text/plain" });
  res.end(greeting(req.url));
});

if (require.main === module) {
  server.listen(PORT);
}

module.exports = { server };
