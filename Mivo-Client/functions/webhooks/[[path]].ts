export const onRequest: PagesFunction = async (context) => {
  const url = new URL(context.request.url);
  url.hostname = "enjoy-api.nasriddinov.dev";
  url.port = "443";
  url.pathname = "/mivo" + url.pathname;
  return fetch(new Request(url.toString(), context.request));
};
