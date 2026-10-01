/* Only domain + method + completion metadata leave this function. */
function browserMetadata(details, hosts, phase) {
  try {
    const url = new URL(details.url);
    if (url.protocol !== 'https:' || !Object.hasOwn(hosts, url.hostname) || details.tabId < 0) return null;
    if (!['main_frame','sub_frame','xmlhttprequest'].includes(details.type)) return null;
    if (!/^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|CONNECT|TRACE)$/.test(details.method)) return null;
    return {timestamp:new Date(details.timeStamp).toISOString(),host:url.hostname,method:details.method,
      kind:details.type==='main_frame'?'navigation':'request',phase,
      status_code:phase==='completed'&&details.statusCode>=100&&details.statusCode<=599?details.statusCode:null};
  } catch { return null; }
}
if(typeof module!=='undefined')module.exports={browserMetadata};
