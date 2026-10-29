"use strict";

/** Greeting for a request path; "/" gets the generic form. */
function greeting(path) {
  const name = (path || "/").replace(/^\//, "").trim();
  return name ? `hello, ${name}\n` : "hello\n";
}

module.exports = { greeting };
