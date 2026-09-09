export const onRequest: PagesFunction = async (context) => {
  const url = new URL(context.request.url);
  url.hostname = "enjoy-api.nasriddinov.dev";
  url.port = "443";
  url.pathname = url.pathname.replace(/^\/api/, "/mivo");
  return fetch(new Request(url.toString(), context.request));
};
