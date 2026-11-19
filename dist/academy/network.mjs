// Small routed IPv4 model: fixed links, connected/static routes, request and reply.
// It models reachability and DNS/service availability, not physical packet timing.
const FAULTS = ['mask', 'gateway', 'dns', 'route'];
const NAMES = {mask: '为什么服务器看起来在本地？', gateway: '网关在哪里？', dns: 'IP 能通，网站为什么打不开？', route: '路由器知道下一站吗？', transfer: '换个网络，再排查一次'};
export const networkHints = {
  mask: ['对比本机与服务器地址，检查本机把目标判断为本地还是远端。', '前缀过短会把实际不同链路误认为同一个子网。', '查看拓扑中的 /24 网段边界，修正客户端前缀后再次测试。'],
  gateway: ['先测试默认网关是否可达，再检查远端服务器。', '远端目的地址需要交给本地链路上存在的路由器接口。', '将客户端网关设为 R1 在办公网的接口地址，再测试网站。'],
  dns: ['分别测试服务器 IP 与域名解析，比较两种结果。', 'IP 路径正常不保证配置的 DNS 服务器存在且提供服务。', '将客户端 DNS 设为拓扑中 DNS 主机的地址，然后重新访问域名。'],
  route: ['比较到 R1 与到服务器的路径，找出在哪个节点停止。', '路由器需要匹配目的网段的路由；请求和回复都需要路径。', '为 R1 启用到服务器网段、经 R2 互联口转发的路由，再验证。'],
};

function uintIp(text) {
  if (typeof text !== 'string' || !/^\d{1,3}(\.\d{1,3}){3}$/.test(text)) throw new Error('请输入完整 IPv4 地址。');
  const bytes = text.split('.').map(Number);
  if (bytes.some(n => n < 0 || n > 255)) throw new Error('IPv4 每段需在 0～255 之间。');
  return bytes.reduce((v, n) => v * 256 + n, 0);
}
function prefixSize(prefix) {
  if (!Number.isInteger(prefix) || prefix < 0 || prefix > 32) throw new Error('IPv4 前缀需为 0～32 的整数。');
  return 2 ** (32 - prefix);
}
function sameNetwork(a, b, prefix) { const size = prefixSize(prefix); return Math.floor(uintIp(a) / size) === Math.floor(uintIp(b) / size); }
function parseRoute(route) {
  if (typeof route.destination !== 'string') throw new Error('路由目的需包含前缀。');
  const match = route.destination.match(/^(\d{1,3}(?:\.\d{1,3}){3})\/(\d{1,2})$/);
  if (!match) throw new Error('路由目的请填写 IPv4/前缀。');
  uintIp(match[1]); prefixSize(Number(match[2])); uintIp(route.nextHop);
  return {...route, address: match[1], prefix: Number(match[2])};
}

export function createNetworkScenario(kind = 'mask', seed = 1) {
  if (![...FAULTS, 'transfer'].includes(kind)) throw new Error('未知网络任务。');
  if (!Number.isSafeInteger(seed) || seed < 1 || seed > 1000000) throw new Error('变式种子需为 1～1000000 的整数。');
  const x = 1 + (seed * 37 % 200), fault = kind === 'transfer' ? FAULTS[(seed - 1) % FAULTS.length] : kind;
  const base = `10.${x}`, lan = `${base}.1`, remote = `${base}.2`, transit = `${base}.10`;
  const port = (ip, prefix, segment) => ({ip, prefix, segment});
  const nodes = [
    {id: 'client', name: '学习电脑', interfaces: [port(`${lan}.10`, 24, 'office')], gateway: `${lan}.1`},
    {id: 'r1', name: 'R1', interfaces: [port(`${lan}.1`, 24, 'office'), port(`${transit}.1`, 30, 'transit')], routes: []},
    {id: 'r2', name: 'R2', interfaces: [port(`${transit}.2`, 30, 'transit'), port(`${remote}.1`, 24, 'servers')],
      routes: [{destination: `${lan}.0/24`, nextHop: `${transit}.1`, enabled: true}]},
    {id: 'dns', name: 'DNS', interfaces: [port(`${remote}.53`, 24, 'servers')], gateway: `${remote}.1`},
    {id: 'web', name: 'Web', interfaces: [port(`${remote}.80`, 24, 'servers')], gateway: `${remote}.1`},
  ];
  const config = {prefix: 24, gateway: `${lan}.1`, dns: `${remote}.53`,
    route: {destination: `${remote}.0/24`, nextHop: `${transit}.2`, enabled: true}};
  const solution = structuredClone(config);
  if (fault === 'mask') config.prefix = 16;
  if (fault === 'gateway') config.gateway = `${lan}.254`;
  if (fault === 'dns') config.dns = `${remote}.99`;
  if (fault === 'route') config.route.enabled = false;
  return {version: 1, kind, seed, fault, title: NAMES[kind], nodes, config, solution,
    domain: 'portal.example.test', server: `${remote}.80`, dnsServer: `${remote}.53`,
    office: `${lan}.0/24`, servers: `${remote}.0/24`, transit: `${transit}.0/30`,
    objective: '让学习电脑能按域名访问服务器，并用诊断结果说明修复依据。'};
}

function configuredNodes(scenario, config) {
  prefixSize(config.prefix); uintIp(config.gateway); uintIp(config.dns);
  if (!config.route || typeof config.route.enabled !== 'boolean') throw new Error('路由开关无效。');
  parseRoute(config.route);
  const nodes = structuredClone(scenario.nodes);
  nodes.find(n => n.id === 'client').interfaces[0].prefix = config.prefix;
  nodes.find(n => n.id === 'client').gateway = config.gateway;
  nodes.find(n => n.id === 'r1').routes = [structuredClone(config.route)];
  return nodes;
}

function forward(nodes, fromId, destination) {
  uintIp(destination);
  let current = nodes.find(n => n.id === fromId);
  if (!current) throw new Error('起点不存在。');
  const trace = [], visited = new Set();
  const fail = message => ({ok: false, message, trace: [...trace, {node: current.id, name: current.name, message}]});
  for (let ttl = 12; ttl > 0; ttl--) {
    if (current.interfaces.some(p => p.ip === destination)) {
      trace.push({node: current.id, name: current.name, message: `到达目标 ${destination}。`});
      return {ok: true, message: '目的主机可达。', trace};
    }
    if (visited.has(current.id)) return fail('出现转发环路，终止教学探测。');
    visited.add(current.id);
    let output, nextHop;
    if (!current.routes) {
      output = current.interfaces[0];
      const local = sameNetwork(output.ip, destination, output.prefix);
      nextHop = local ? destination : current.gateway;
      trace.push({node: current.id, name: current.name, message: local ?
        `按 /${output.prefix} 判断 ${destination} 在本地链路，尝试直接寻址。` : `目标不在本地 /${output.prefix}，交给网关 ${nextHop}。`});
      if (!nextHop || !sameNetwork(output.ip, nextHop, output.prefix)) return fail('网关不在本机声明的子网内，无法建立下一跳。');
    } else {
      const candidates = current.interfaces.filter(p => sameNetwork(p.ip, destination, p.prefix))
        .map(p => ({prefix: p.prefix, output: p, nextHop: destination, connected: true}));
      for (const raw of current.routes.filter(r => r.enabled)) {
        const route = parseRoute(raw);
        if (sameNetwork(route.address, destination, route.prefix)) candidates.push({...route, connected: false});
      }
      candidates.sort((a, b) => b.prefix - a.prefix || Number(b.connected) - Number(a.connected));
      const route = candidates[0];
      if (!route) return fail(`没有匹配 ${destination} 的路由，转发停止。`);
      nextHop = route.nextHop;
      output = route.output || [...current.interfaces].sort((a, b) => b.prefix - a.prefix)
        .find(p => sameNetwork(p.ip, nextHop, p.prefix));
      if (!output) return fail(`路由下一跳 ${nextHop} 不在任何直连链路。`);
      trace.push({node: current.id, name: current.name, message: route.connected ?
        `匹配直连 /${route.prefix}，在 ${output.segment} 链路寻找目标。` : `匹配 /${route.prefix} 路由，下一跳 ${nextHop}。`});
    }
    const next = nodes.find(n => n.id !== current.id && n.interfaces.some(p => p.segment === output.segment && p.ip === nextHop));
    if (!next) return fail(`在 ${output.segment} 链路找不到下一跳 ${nextHop}，ARP 无响应。`);
    current = next;
  }
  return fail('超过教学模型的跳数上限。');
}

function roundtrip(nodes, target) {
  const outward = forward(nodes, 'client', target);
  if (!outward.ok) return {...outward, outward: outward.trace, reply: []};
  const recipient = nodes.find(n => n.interfaces.some(p => p.ip === target));
  const reply = forward(nodes, recipient.id, nodes.find(n => n.id === 'client').interfaces[0].ip);
  return {ok: reply.ok, message: reply.ok ? '请求和回复路径均可达。' : '请求已到达，但回复路径失败：' + reply.message,
    trace: [...outward.trace.map(t=>({...t,phase:'请求'})), ...reply.trace.map(t=>({...t,phase:'回复'}))], outward: outward.trace, reply: reply.trace};
}

export function probeNetwork(scenario, config, tool = 'web') {
  const nodes = configuredNodes(scenario, config);
  if (tool === 'gateway') return {...roundtrip(nodes, config.gateway), tool};
  if (tool === 'server' || tool === 'path') return {...roundtrip(nodes, scenario.server), tool};
  if (!['dns', 'web'].includes(tool)) throw new Error('未知诊断操作。');
  const dns = roundtrip(nodes, config.dns);
  if (!dns.ok) return {...dns, ok: false, tool, message: 'DNS 查询无法完成：' + dns.message};
  if (config.dns !== scenario.dnsServer) return {...dns, ok: false, tool, message: '目标主机可达，但未提供本实验域名解析服务。'};
  const dnsMessage = `${scenario.domain} → ${scenario.server}`;
  if (tool === 'dns') return {...dns, tool, message: 'DNS 解析成功：' + dnsMessage};
  const web = roundtrip(nodes, scenario.server);
  return {...web, tool, dns: dnsMessage, trace: [...dns.trace.map(t=>({...t,phase:'DNS '+t.phase})), ...web.trace.map(t=>({...t,phase:'网站 '+t.phase}))],
    message: web.ok ? `网站访问成功：${dnsMessage}；请求与回复路径正常。` : '域名解析成功，但服务器路径失败：' + web.message};
}
