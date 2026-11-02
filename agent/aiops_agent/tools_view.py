"""Network operations workspace presentation and existing query interactions."""
from .path_visualization import PATH_GRAPH_STYLE, PATH_GRAPH_SCRIPT

TOOLS_PAGE = r'''<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AI Networking OPS Tools</title><style>
:root{color-scheme:light;--bg:#f2f5fb;--panel:#fff;--line:#e0e7f1;--text:#17243c;--muted:#65748c;--green:#137950;--yellow:#9a630c;--red:#b42318;--blue:#405be8}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 95% 25%,#e5e3ff88,transparent 45%),var(--bg);color:var(--text);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}a{color:var(--blue);text-decoration:none}button,input,select{font:inherit}button,a,input,select,summary{-webkit-tap-highlight-color:transparent}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #818cf8;outline-offset:4px}[hidden]{display:none!important}.shell{max-width:1440px;margin:auto;padding:0 40px}.masthead{height:80px;display:flex;align-items:center;justify-content:space-between;gap:20px}.brand{display:flex;align-items:center;gap:12px;font-weight:800;letter-spacing:-.02em}.brand-mark{display:grid;place-items:center;width:39px;height:39px;border-radius:13px;color:#fff;background:linear-gradient(135deg,#5065ef,#9270ee);box-shadow:0 6px 16px #6a69ec33}.brand small{display:block;font-size:10px;color:var(--muted);letter-spacing:.16em;font-weight:500}.monitor-home{font-size:12px;color:#5264b3;text-align:right}.masthead-note{color:var(--muted);font-size:12px}.hero{position:relative;isolation:isolate;overflow:hidden;display:grid;grid-template-columns:1.35fr 1fr;align-items:center;min-height:278px;padding:40px 48px;border-radius:24px;background:radial-gradient(ellipse at 85% 110%,#3a3a8870,transparent 60%),linear-gradient(115deg,#152741,#1d2954 55%,#28366d);color:#fff;box-shadow:0 18px 36px #25356018}.hero:before{content:"";position:absolute;inset:0;z-index:-1;opacity:.09;background-image:linear-gradient(#b8c9ff 1px,transparent 1px),linear-gradient(90deg,#b8c9ff 1px,transparent 1px);background-size:36px 36px;mask-image:linear-gradient(90deg,transparent,#000)}.eyebrow{font-size:10px;font-weight:700;letter-spacing:.2em;color:#aebfff}.hero h1{font-size:clamp(28px,3vw,42px);line-height:1.2;letter-spacing:-.045em;margin:15px 0}.hero h1 span{color:#8de8e1}.hero p{max-width:500px;color:#bdcbe4;font-size:14px;margin:0}.hero-tags{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px}.hero-tags span{border:1px solid #8ca7de30;border-radius:30px;padding:4px 12px;font-size:11px;color:#d5e1fa;background:#ffffff08}.network-art{width:100%;max-height:245px;filter:drop-shadow(0 14px 20px #060b2866)}.network-art .signal{stroke-dasharray:7 10;animation:signal 12s linear infinite}@keyframes signal{to{stroke-dashoffset:-170}}.section-label{display:flex;align-items:center;gap:10px;margin:28px 0 14px;font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)}.section-label:after{content:"";height:1px;background:var(--line);flex:1}.launchers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.launcher{display:flex;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);padding:20px;border-radius:16px;color:var(--text);box-shadow:0 4px 10px #26365603;transition:transform .18s,box-shadow .18s,border-color .18s}.locator-entry{border-color:#aadbd3;background:linear-gradient(120deg,#effbf8,#fff)}.launcher:hover{transform:translateY(-4px);box-shadow:0 10px 24px #26365612;border-color:#a5b4fc}.launcher strong{display:block;font-size:14px}.launcher small{display:block;font-size:10px;color:var(--muted);margin-top:3px}.launcher .arrow{margin-left:auto;color:#8996ab}.icon{display:grid;place-items:center;flex-shrink:0;width:42px;height:42px;border-radius:13px;background:#eef0ff;color:#6562d9}.icon svg,.brand-mark svg{width:22px;height:22px}.teal{background:#e7f7f5;color:#218b82}.amber{background:#fff3e3;color:#b67c22}.pink{background:#f9ecf6;color:#b35c9c}.workspace{margin:34px 0 40px}.workspace-title{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:20px}.workspace-title h2{font-size:23px;letter-spacing:-.03em;margin:0}.workspace-title p{font-size:12px;color:var(--muted);margin:4px 0 0}.tabs{display:flex;gap:6px;background:#e6ebf5;padding:5px;border-radius:13px}.tabs button{height:42px;border:0;background:transparent;color:#68758d;border-radius:9px;padding:0 20px;font-weight:600;white-space:nowrap}.tabs button[aria-selected=true]{background:#fff;color:#4058cc;box-shadow:0 3px 9px #26365612}.tool-panel{min-width:0}.tool-card{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:30px;box-shadow:0 6px 24px #26365604;position:relative;overflow:hidden}.tool-card:before{content:"";position:absolute;top:0;left:0;width:100%;height:3px;background:linear-gradient(90deg,#6b79ef,#86d9de,#ba9aef)}.card-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:26px}.heading-copy{display:flex;align-items:center;gap:13px}.heading-copy h2{font-size:17px;margin:0}.heading-copy p{margin:3px 0 0;font-size:12px;color:var(--muted)}.number{font-size:11px;letter-spacing:.06em;color:#7283a2;background:#f1f4fa;border:1px solid var(--line);padding:5px 10px;border-radius:7px}.scope{display:flex;align-items:center;gap:10px;flex-shrink:0}.scope label{margin:0;white-space:nowrap}.scope select{min-width:150px;width:auto}.endpoints{display:grid;grid-template-columns:minmax(0,1fr) 44px minmax(0,1fr);align-items:center;gap:14px}.endpoint{min-width:0;padding:23px;background:linear-gradient(140deg,#f6f8ff,#fbfcff);border:1px solid #e2e7f5;border-radius:15px}.endpoint.destination{background:linear-gradient(140deg,#f3faf9,#fcfefe);border-color:#dcece9}.endpoint-title{display:flex;align-items:center;gap:10px;margin-bottom:18px;font-weight:700;font-size:13px}.endpoint-letter{display:grid;place-items:center;width:28px;height:28px;background:#e4e9ff;color:#5966d2;border-radius:8px;font-size:12px}.destination .endpoint-letter{background:#def2ed;color:#238f80}.direction{text-align:center;color:#9aa7bf;font-size:24px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.wide{grid-column:1/-1}label{display:block;color:#5e6e87;font-size:12px;margin-bottom:7px}input,select,button{height:44px;border:1px solid #d9e1ed;border-radius:9px;padding:0 12px;background:#fff;color:var(--text);max-width:100%}input,select{width:100%;min-width:0}input::placeholder{color:#96a1b4}input:hover,select:hover{border-color:#b8c6dc}input:disabled,select:disabled{background:#edf1f7;color:#96a1b4}button{cursor:pointer;background:#f5f7fd;font-weight:600}button:hover{border-color:#9dace4}button:disabled{cursor:wait;opacity:.6}.analysis-settings{margin-top:24px;display:grid;grid-template-columns:1fr 1fr;gap:20px;padding-top:24px;border-top:1px solid var(--line)}.flow-options{margin-top:22px;border:1px solid #e1e4f5;background:#f9f9ff;border-radius:13px;padding:20px}.flow-options h3{font-size:13px;margin:0 0 16px;color:#6963ae}.flow-options .form-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.advanced{margin-top:16px}.advanced summary{font-size:12px;color:#687990;cursor:pointer}.advanced .form-grid{margin-top:14px}.actions{display:flex;align-items:center;gap:12px;margin-top:24px;flex-wrap:wrap}.run-row{justify-content:space-between}.run-row p{margin:0;font-size:12px;color:var(--muted);max-width:70%}.primary{display:flex;align-items:center;justify-content:center;gap:10px;min-width:180px;height:47px;background:linear-gradient(110deg,#4668e8,#7162de);color:#fff;border:0;box-shadow:0 5px 12px #5f69de25}.primary:hover{filter:brightness(1.08)}.primary svg{width:18px;height:18px}.result{min-width:0;display:grid;gap:14px;margin-top:20px}.path-results{margin-top:24px}.path-results:before{background:linear-gradient(90deg,#73cdbd,#96bfe5)}.result-empty{display:flex;align-items:center;justify-content:center;gap:22px;padding:38px 20px;border:1px dashed #d6deed;background:radial-gradient(ellipse at center,#f3f6ff,#fbfcfe);border-radius:14px}.result-empty .empty-glyph{font-size:28px;color:#8c9bd3;border:1px solid #dfe6f4;border-radius:16px;padding:12px 19px;background:#fff}.result-empty h3{margin:0 0 4px;font-size:14px}.result-empty p{margin:0;font-size:12px;color:#7e8ba1}.result-heading{margin-bottom:0}.message{color:var(--muted);background:#f6f8fc;border:1px solid var(--line);border-radius:9px;padding:14px;overflow-wrap:anywhere}.summary{display:flex;flex-wrap:wrap;gap:8px}.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:7px;padding:5px 10px;color:#53637b;background:#f8faff;font-size:12px;max-width:100%;overflow-wrap:anywhere}.pill.good{color:var(--green);border-color:#b7e2c1;background:#f0fdf4}.pill.warn{color:var(--yellow);border-color:#f2d49b;background:#fffbeb}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#f6f8fc;color:#60708c;font-weight:600;white-space:nowrap}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}h3{margin:2px 0 8px;font-size:14px}.path{display:grid;gap:10px}.step{border:1px solid var(--line);border-radius:10px;padding:15px;background:#fff}.step-title{font-weight:700;margin-bottom:8px}.step-meta{color:var(--muted);font-size:12px}.route-evidence{border-top:1px solid var(--line);margin-top:24px;padding-top:18px}.route-evidence>summary{cursor:pointer;color:#657592;font-size:12px}.route-evidence .actions{margin-top:14px}footer{display:flex;justify-content:space-between;gap:16px;padding:0 0 30px;color:#8a98ae;font-size:11px}.mac-layout{display:grid;grid-template-columns:1fr 1.25fr;gap:30px}.mac-guide{background:linear-gradient(130deg,#f3f5ff,#f6fbfc);border-radius:14px;padding:24px;border:1px solid var(--line)}.mac-guide h3{color:#506397}.mac-guide p{color:var(--muted);font-size:12px}.mac-guide code{display:inline-block;padding:4px 9px;border:1px solid #dce5f2;background:#fff;border-radius:6px;margin-top:4px;color:#536487;font-size:12px}.path-graph-scroll{border-top:1px solid var(--line)}
@media(min-width:1500px){.shell{padding:0 48px}.hero{min-height:300px}}@media(max-width:1000px){.shell{padding:0 24px}.launchers{grid-template-columns:repeat(2,minmax(0,1fr))}.hero{padding:32px}.hero h1{font-size:32px}.workspace-title{align-items:start}.endpoints{gap:8px;grid-template-columns:minmax(0,1fr) 26px minmax(0,1fr)}.endpoint{padding:18px}.tool-card{padding:24px}.flow-options .form-grid{grid-template-columns:1fr 1fr}}@media(max-width:700px){.shell{padding:0 16px}.masthead{height:68px}.monitor-home{font-size:12px;color:#5264b3;text-align:right}.masthead-note{display:none}.hero{grid-template-columns:1fr;padding:28px;border-radius:20px;min-height:0}.hero h1{font-size:30px}.network-art{display:none}.hero p{font-size:12px}.hero-tags{margin-top:20px}.launchers{gap:10px}.launcher{padding:14px 12px;gap:10px}.launcher .icon{width:34px;height:34px;border-radius:10px}.launcher strong{font-size:12px}.launcher .arrow{display:none}.launcher small{font-size:9px}.workspace{margin-top:28px}.workspace-title{flex-direction:column;align-items:stretch;gap:16px}.tabs button{flex:1;padding:0 10px;font-size:12px}.card-heading{align-items:start;flex-wrap:wrap;gap:16px}.scope{width:100%;justify-content:space-between}.scope select{flex:1}.tool-card{padding:20px 16px;border-radius:16px}.endpoints{grid-template-columns:1fr;gap:12px}.direction{transform:rotate(90deg);font-size:20px;line-height:1}.form-grid,.analysis-settings,.flow-options .form-grid{grid-template-columns:1fr}.form-grid{gap:14px}.flow-options{padding:16px}.run-row{gap:16px}.run-row p{max-width:100%;font-size:11px}.primary{width:100%}.result-empty{padding:26px 12px;gap:14px}.result-empty .empty-glyph{padding:8px 13px;font-size:22px}.result-empty p{font-size:11px}.mac-layout{grid-template-columns:1fr}.mac-guide{padding:18px}.number{font-size:10px}footer{flex-direction:column;gap:4px}.path-branch-header{padding:14px}.path-evidence{margin:12px}}@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}

</style></head><body>
<header class="shell"><div class="masthead"><a class="brand" href="/tools"><span class="brand-mark"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="3" width="8" height="6" rx="2"/><rect x="2" y="16" width="7" height="5" rx="1.5"/><rect x="15" y="16" width="7" height="5" rx="1.5"/><path d="M12 9v4H5v3m7-3h7v3"/></svg></span><span>AI Networking<small>OPERATIONS WORKSPACE</small></span></a><a class="monitor-home" href="/monitor?lang=zh">◈ 监控首页 / Network Pulse ↗</a></div><div class="hero"><div><div class="eyebrow">NETWORK INTELLIGENCE / 运维工作台</div><h1>AI Networking<br><span>OPS Tools</span></h1><p>从一个地址出发，看清设备连接与流量路径。<br>把查找、分析与验证，放进一个清晰的工作空间。</p><div class="hero-tags"><span>MAC / IP 定位</span><span>端到端路径</span><span>策略与 NAT 推演</span></div></div><svg class="network-art" viewBox="0 0 440 240" fill="none" aria-hidden="true"><defs><linearGradient id="hub" x1="160" y1="70" x2="265" y2="170" gradientUnits="userSpaceOnUse"><stop stop-color="#7585f8"/><stop offset="1" stop-color="#4a5ba7"/></linearGradient></defs><ellipse cx="220" cy="121" rx="183" ry="97" stroke="#9aacf6" stroke-opacity=".12"/><ellipse cx="220" cy="121" rx="130" ry="67" stroke="#9aacf6" stroke-opacity=".16"/><g stroke="#6e86cd" stroke-width="1.5"><path d="M80 63 215 117 363 59M215 117 350 189M215 117 84 188"/></g><g class="signal" stroke="#91e6df" stroke-width="2"><path d="M80 63 215 117 350 189"/><path d="M84 188 215 117 363 59"/></g><g fill="#233760" stroke="#6d88be"><rect x="49" y="42" width="62" height="43" rx="12"/><rect x="332" y="38" width="62" height="43" rx="12"/><rect x="53" y="166" width="62" height="43" rx="12"/><rect x="319" y="167" width="62" height="43" rx="12"/></g><g stroke="#b4c9ef" stroke-width="2" stroke-linecap="round"><path d="M64 57h32M64 68h20m264-15h32m-32 11h20M68 181h32m-32 11h20m246-10h32m-32 11h20"/></g><rect x="166" y="72" width="100" height="94" rx="25" fill="url(#hub)" stroke="#a8b6ff"/><rect x="188" y="93" width="56" height="23" rx="6" fill="#dce5ff" fill-opacity=".12" stroke="#d0d9ff"/><path d="M196 105h22m-2 11v14m-21 0h42m-42 0v12m21-12v12m21-12v12" stroke="#d0d9ff" stroke-width="2" stroke-linecap="round"/><circle cx="235" cy="104" r="2" fill="#9cf2de"/><g fill="#98dace"><circle cx="132" cy="85" r="3"/><circle cx="290" cy="89" r="3"/><circle cx="291" cy="158" r="3"/><circle cx="139" cy="159" r="3"/></g></svg></div><div class="section-label">EXPLORE / 快捷入口</div><nav class="launchers" aria-label="快捷入口"><a class="launcher locator-entry" href="#mac" data-open-tool="mac"><span class="icon teal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></svg></span><span><strong>IP / MAC 定位</strong><small>查 IP、MAC 所在设备与接口</small></span><span class="arrow" aria-hidden="true">↗</span></a><a class="launcher" href="#path" data-open-tool="path"><span class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h7a4 4 0 0 1 0 8h-4a4 4 0 0 0 0 8h5"/></svg></span><span><strong>路径分析</strong><small>A → B 设备路径与策略推演</small></span><span class="arrow" aria-hidden="true">↗</span></a><a class="launcher" href="/topology"><span class="icon "><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="3" width="8" height="6" rx="2"/><rect x="2" y="16" width="7" height="5" rx="1.5"/><rect x="15" y="16" width="7" height="5" rx="1.5"/><path d="M12 9v4H5v3m7-3h7v3"/></svg></span><span><strong>网络拓扑</strong><small>Network topology</small></span><span class="arrow" aria-hidden="true">↗</span></a><a class="launcher" href="/firewall/?lang=zh"><span class="icon teal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m8 12 3 3 5-6"/></svg></span><span><strong>防火墙策略</strong><small>Firewall policy</small></span><span class="arrow" aria-hidden="true">↗</span></a><a class="launcher" href="/config/ui?lang=zh"><span class="icon amber"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="5" rx="2"/><path d="M5 8v12h14V8m-10 5h6"/></svg></span><span><strong>配置与漂移</strong><small>Configuration & drift</small></span><span class="arrow" aria-hidden="true">↗</span></a><a class="launcher" href="/health?lang=zh"><span class="icon pink"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12h4l3-7 4 14 3-7h4"/></svg></span><span><strong>网络健康</strong><small>Network health</small></span><span class="arrow" aria-hidden="true">↗</span></a></nav></header><main class="shell"><div class="workspace"><div class="workspace-title"><div><h2>网络诊断工作台</h2><p>选择一个工具，开始探索你的网络。</p></div><div class="tabs" role="tablist" aria-label="诊断工具"><button id="tab-path" role="tab" aria-controls="panel-path" aria-selected="true" data-tool="path">↗ &nbsp;路径分析</button><button id="tab-mac" role="tab" aria-controls="panel-mac" aria-selected="false" tabindex="-1" data-tool="mac">⌕ &nbsp;MAC / IP 定位</button></div></div>
<div id="panel-path" class="tool-panel" role="tabpanel" aria-labelledby="tab-path">
<section class="tool-card"><div class="card-heading"><div class="heading-copy"><span class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 5h7a4 4 0 0 1 0 8h-4a4 4 0 0 0 0 8h5m3-4 2 2-2 2"/></svg></span><div><h2>端到端路径分析</h2><p>End-to-End Path · 从起点到终点</p></div></div><div class="scope"><label for="pathSite">站点 / Site</label><select id="pathSite" onchange="loadPathOptions()"></select></div></div>
<div class="endpoints"><div class="endpoint "><div class="endpoint-title"><span class="endpoint-letter">A</span>起点 / Source</div><div class="form-grid"><div class="wide"><label for="srcQuery">MAC、IP 或 device:interface</label><input id="srcQuery" placeholder="例如 10.100.101.111"></div><div><label for="srcDevice">设备 / Device</label><select id="srcDevice" ></select></div><div><label for="srcVrf">VRF / 路由域</label><select id="srcVrf" ></select></div></div></div><div class="direction" aria-hidden="true">→</div><div class="endpoint destination"><div class="endpoint-title"><span class="endpoint-letter">B</span>终点 / Destination</div><div class="form-grid"><div class="wide"><label for="dstQuery">MAC、IP 或 device:interface</label><input id="dstQuery" placeholder="例如 10.200.123.212"></div><div><label for="dstDevice">设备 / Device</label><select id="dstDevice" ></select></div><div><label for="dstVrf">VRF / 路由域</label><select id="dstVrf" ></select></div></div></div></div>
<div class="analysis-settings"><div><label for="pathAnalysis">分析方式 / Analysis</label><select id="pathAnalysis" onchange="toggleFlowFields()"><option value="routing">路径推演 / Routing</option><option value="flow">流量 + PA 策略 / NAT</option></select></div><div><label for="pathMode">路径模式 / Path mode</label><select id="pathMode" ><option value="auto">自动选择 / Auto</option><option value="routed">路由查询 / Route lookup</option><option value="physical">物理连通 / Physical connectivity</option></select></div></div>
<div id="flowOptions" class="flow-options" hidden><h3>流量条件 / Flow parameters</h3><div class="form-grid"><div><label for="flowProtocol">协议 / Protocol</label><select id="flowProtocol" ><option value="any">未指定 / Any</option><option value="tcp">TCP</option><option value="udp">UDP</option><option value="icmp">ICMP</option><option value="icmpv6">ICMPv6</option></select></div><div><label for="flowSourcePort">源端口 / Source port</label><input id="flowSourcePort" type="number" placeholder="可选 / Optional" min="0" max="65535"></div><div><label for="flowDestinationPort">目的端口 / Destination port</label><input id="flowDestinationPort" type="number" placeholder="例如 50001" min="0" max="65535"></div></div><details class="advanced"><summary>高级条件：App-ID 与入接口 / Advanced</summary><div class="form-grid"><div><label for="flowApplication">App-ID 假设 / Assumed application</label><input id="flowApplication" type="text" placeholder="未知时留空 / Leave blank if unknown" ></div><div><label for="flowIngress">源设备入口 / Source ingress</label><input id="flowIngress" type="text" placeholder="设备自身发起时留空 / Optional" ></div></div></details><p id="flowHint" class="message" hidden>未指定协议或端口时，仅在所有可能命中的规则均允许、且 NAT 结果明确时继续。补充实际流量条件可确定具体规则。<br>Supply protocol and ports to resolve the exact rule. Address any-to-any does not imply service any.</p><p class="step-meta">按新建连接推演；交换起点与终点不代表已建立会话的回包。 / New connections only.</p></div>
<div class="actions run-row"><p>基于已采集的路由、拓扑与策略证据进行分析。</p><button class="primary" id="traceButton" type="button" onclick="lookupPath()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h16m-6-6 6 6-6 6"/></svg>分析路径 / Trace Path</button></div></section>
<section class="tool-card path-results" id="path-results"><div class="card-heading result-heading"><div class="heading-copy"><span class="icon teal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="3" width="8" height="6" rx="2"/><rect x="2" y="16" width="7" height="5" rx="1.5"/><rect x="15" y="16" width="7" height="5" rx="1.5"/><path d="M12 9v4H5v3m7-3h7v3"/></svg></span><div><h2>设备路径图</h2><p>Device Path · 逐跳查看设备与转发证据</p></div></div><span class="number">RESULT / 分析结果</span></div><div id="pathResult" class="result" aria-live="polite"><div class="result-empty"><span class="empty-glyph" aria-hidden="true">A → B</span><div><h3>你的下一条路径，从这里开始</h3><p>填写起点与终点，点击“分析路径”查看结果。</p></div></div></div><details class="route-evidence"><summary>源设备路由表 / Source routing table</summary><div class="actions"><button type="button" onclick="showRoutes()">查看源设备 / VRF 路由</button></div><div id="routesResult" class="result" aria-live="polite"></div></details></section></div>
<div id="panel-mac" class="tool-panel" role="tabpanel" aria-labelledby="tab-mac" hidden><section class="tool-card"><div class="card-heading"><div class="heading-copy"><span class="icon teal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></svg></span><div><h2>MAC / IP 定位</h2><p>Endpoint Lookup · 找到地址所在的设备与接口</p></div></div><span class="number">LOOKUP / 地址查找</span></div><div class="mac-layout"><div><div class="form-grid"><div class="wide"><label for="macQuery">MAC 或 IP 地址</label><input id="macQuery" placeholder="aabb.cc80.b000 或 10.110.0.11"></div><div><label for="macDevice">设备 / Device</label><select id="macDevice" ></select></div><div><label for="macVrf">IP 查询路由域 / VRF</label><select id="macVrf" ></select></div></div><div class="actions"><button id="macButton" class="primary" type="button" onclick="lookupMac()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></svg>查找地址 / Lookup</button></div></div><aside class="mac-guide"><h3>一个地址，关联多种网络证据</h3><p>查询 ARP、Local MAC、Overlay MAC 与 CLF 记录，查看设备、接口、VLAN 及 VRF。</p><code>MAC → 设备 / 接口</code> <code>IP → ARP / MAC</code><p>IP 查询可选择 VRF；MAC 查询无需指定 VRF。</p></aside></div><div id="macResult" class="result" aria-live="polite"><div class="result-empty"><span class="empty-glyph" aria-hidden="true">⌕</span><div><h3>快速定位一个地址</h3><p>输入 MAC 或 IP，查看已采集的匹配记录。</p></div></div></div></section></div></div></main><footer class="shell"><span>AI Networking · OPS Tools</span><span>配置推演与采集证据 · 实际转发仍需结合报文或会话验证</span></footer>
<script>

    const deviceSelects = ["macDevice", "srcDevice", "dstDevice"];
    const vrfSelects = ["macVrf", "srcVrf", "dstVrf"];

    let paChecks = [];
    selectTool(location.hash === "#mac" ? "mac" : "path");
    toggleFlowFields();
    loadOptions();
    bindVrfToggle("macQuery", "macVrf");



    async function lookupMac() {
      const query = document.getElementById("macQuery").value.trim();
      const params = new URLSearchParams({
        query,
        device: document.getElementById("macDevice").value,
        vrf: isIp(query) ? document.getElementById("macVrf").value : "all",
      });
      const button=document.getElementById("macButton"); button.disabled=true;
      try { await renderLookup("/tools/mac?" + params.toString(), "macResult"); } finally { button.disabled=false; }
    }

    function toggleFlowFields() {
      const enabled = document.getElementById("pathAnalysis").value === "flow";
      for (const id of ["flowProtocol", "flowSourcePort", "flowDestinationPort", "flowApplication", "flowIngress"]) document.getElementById(id).disabled = !enabled;
      document.getElementById("flowHint").hidden = !enabled;
      document.getElementById("flowOptions").hidden = !enabled;
      if (enabled && document.getElementById("pathMode").value === "physical") document.getElementById("pathMode").value = "auto";
    }

    async function lookupPath() {
      const src = document.getElementById("srcQuery").value.trim();
      const dst = document.getElementById("dstQuery").value.trim();
      const params = new URLSearchParams({
        src,
        dst,
        site: document.getElementById("pathSite").value,
        src_device: document.getElementById("srcDevice").value,
        src_vrf: document.getElementById("srcVrf").value,
        mode: document.getElementById("pathMode").value,
        dst_device: document.getElementById("dstDevice").value,
        dst_vrf: document.getElementById("dstVrf").value,
      });
      params.set("analysis", document.getElementById("pathAnalysis").value);
      if (params.get("analysis") === "flow") {
        for (const [key, id] of [["protocol", "flowProtocol"], ["source_port", "flowSourcePort"], ["destination_port", "flowDestinationPort"], ["application", "flowApplication"], ["ingress_interface", "flowIngress"]]) {
          const input = document.getElementById(id);
          if (!input.checkValidity()) { input.reportValidity(); return; }
          if (input.value.trim()) params.set(key, input.value.trim());
        }
      }
      selectTool("path");
      const button=document.getElementById("traceButton"); button.disabled=true;
      try { await renderPath("/tools/path?" + params.toString(), "pathResult"); document.getElementById("path-results").scrollIntoView({block:"start",behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth"}); } finally { button.disabled=false; }
    }

    async function showRoutes() {
      const target = document.getElementById("routesResult");
      target.textContent = "Loading routes...";
      const params = new URLSearchParams({device: document.getElementById("srcDevice").value, vrf: document.getElementById("srcVrf").value, site: document.getElementById("pathSite").value});
      try {
        const payload = await (await fetch("/api/routes?" + params)).json();
        target.innerHTML = tableSection("Collected RIB routes", payload.routes || [], ["device", "vrf", "prefix", "next_hop", "interface", "protocol", "freshness", "last_success_at"]);
      } catch (error) { target.textContent = "Route request failed: " + error; }
    }

    function bindVrfToggle(queryId, vrfId) {
      const input = document.getElementById(queryId);
      input.addEventListener("input", () => updateVrfState(queryId, vrfId));
      updateVrfState(queryId, vrfId);
    }

    function updateVrfState(queryId, vrfId) {
      const value = document.getElementById(queryId).value.trim();
      const select = document.getElementById(vrfId);
      const enabled = !value || isIp(value);
      select.disabled = !enabled;
      if (!enabled) select.value = "all";
      select.title = enabled ? "Used for IP lookup" : "VRF is not used for MAC lookup";
    }

    async function loadOptions() {
      try {
        const response = await fetch("/tools/options", { cache: "no-store" });
        const payload = await response.json();
        for (const id of deviceSelects) fillSelect(id, payload.devices || [], "All devices");
        for (const id of vrfSelects) fillSelect(id, payload.vrfs || [], "All VRFs");
        fillSelect("pathSite", payload.sites || [], "All sites");
        const params = new URLSearchParams(location.search);
        const preferredSite = params.get("site") || ((payload.sites || []).includes("lab") ? "lab" : "all");
        if (["all", ...(payload.sites || [])].includes(preferredSite)) document.getElementById("pathSite").value = preferredSite;
        await loadPathOptions();
        for (const [key, id] of [["src", "srcQuery"], ["dst", "dstQuery"]]) if (params.has(key)) document.getElementById(id).value = params.get(key);
        for (const [key, id] of [["analysis", "pathAnalysis"], ["protocol", "flowProtocol"], ["source_port", "flowSourcePort"], ["destination_port", "flowDestinationPort"], ["application", "flowApplication"], ["ingress_interface", "flowIngress"]]) {
          if (params.has(key)) document.getElementById(id).value = params.get(key);
        }
        toggleFlowFields();
        if (params.has("src") && params.has("dst")) await lookupPath();
      } catch (error) {
        for (const id of deviceSelects) fillSelect(id, [], "All devices");
        for (const id of vrfSelects) fillSelect(id, [], "All VRFs");
        fillSelect("pathSite", [], "All sites");
      }
    }

    async function loadPathOptions() {
      const site = document.getElementById("pathSite").value;
      const payload = await (await fetch("/tools/options?site=" + encodeURIComponent(site), {cache: "no-store"})).json();
      for (const id of ["srcDevice", "dstDevice", "srcVrf", "dstVrf"]) {
        const current = document.getElementById(id).value;
        const values = id.endsWith("Device") ? payload.devices || [] : payload.vrfs || [];
        fillSelect(id, values, id.endsWith("Device") ? "All devices" : "All VRFs");
        if (values.includes(current)) document.getElementById(id).value = current;
      }
    }

    function fillSelect(id, values, allLabel) {
      const select = document.getElementById(id);
      select.innerHTML = "";
      select.append(new Option(allLabel, "all"));
      for (const value of values) select.append(new Option(value, value));
    }

    async function renderLookup(url, target) {
      const node = document.getElementById(target);
      node.innerHTML = '<div class="message">Loading...</div>';
      try {
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        node.innerHTML = lookupHtml(payload);
      } catch (error) {
        node.innerHTML = '<div class="message">Request failed: ' + escapeHtml(String(error)) + '</div>';
      }
    }

    async function renderPath(url, target) {
      const node = document.getElementById(target);
      node.innerHTML = '<div class="message">Loading...</div>';
      try {
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        node.innerHTML = pathHtml(payload);
      } catch (error) {
        node.innerHTML = '<div class="message">Request failed: ' + escapeHtml(String(error)) + '</div>';
      }
    }

    function lookupHtml(payload) {
      const summary = [
        pill(payload.found ? "Found" : "Not found", payload.found ? "good" : "warn"),
        pill("Query: " + (payload.query || "")),
        pill("Device: " + (payload.device_filter || "all")),
        pill("VRF: " + (payload.vrf_filter || "all")),
        payload.resolved_ip ? pill("IP: " + payload.resolved_ip) : "",
        payload.resolved_mac ? pill("MAC: " + payload.resolved_mac) : "",
      ].join("");
      return '<div class="summary">' + summary + '</div>'
        + tableSection("ARP Matches", payload.arp_matches || [], ["device", "vrf", "ip", "mac", "interface", "site", "role"])
        + tableSection("Local MAC Matches", payload.local_matches || [], ["device", "vlan", "mac", "port", "site", "role"])
        + tableSection("Overlay MAC Matches", payload.overlay_matches || [], ["device", "vlan", "mac", "port", "site", "role"])
        + tableSection("CLF Matches", payload.clf_matches || [], ["device", "vlan", "mac", "port", "site", "location_type"]);
    }

    function pathHtml(payload) {
      pathGraphs = [];
      paChecks = [];
      const candidates = payload.status === "ambiguous"
        ? tableSection("Source candidates — select device and VRF above", payload.source_candidates || [], ["device", "vrf", "interface", "ip", "mac", "vlan"])
          + tableSection("Destination candidates", payload.destination_candidates || [], ["device", "vrf", "interface", "ip", "mac", "vlan"]) : "";
      const branches = payload.paths || [];
      const actionOnly = branches.some(b => (b.path || []).some(s => s.policy_resolution === 'action_only'));
      const paths = branches.map((branch, i) => '<article class="path-branch"><div class="path-branch-header"><h3>Path ' + (i+1) + ' · ' + escapeHtml(branch.status) + '</h3>'
        + '<label><input class="path-port-toggle" type="checkbox" checked>接口 / Interfaces</label></div>'
        + pathGraphHtml(branch, i, payload)
        + (branch.reason ? '<div class="message">' + escapeHtml(branch.reason) + '</div>' : '')
        + '<details class="path-evidence"><summary>完整计算明细 / All evidence (' + (branch.path || []).length + ')</summary><div class="path">' + (branch.path || []).map(stepHtml).join("") + '</div></details></article>').join("");
      return '<div class="summary">' + pill(payload.status || "unavailable", payload.status === "ok" ? "good" : "warn")
        + pill(payload.mode || "auto") + pill(payload.analysis || "routing") + pill("Site: " + (payload.site || "all")) + '</div>'
        + endpointSummary("Source", payload.source || {}) + endpointSummary("Destination", payload.destination || {})
        + (payload.flow ? '<div class="message">新建连接 / New connection · ' + escapeHtml(flowText(payload.flow)) + '</div>' : '')
        + (actionOnly ? '<div class="message">PA 配置推演：所有可能命中的规则均允许，因此路径继续；具体命中规则尚未确定。点击 PA 查看候选规则。此结果不等于实际流量验证。<br>All possible PA security outcomes allow; the exact rule remains unresolved. Click PA for candidate rules. Packet delivery is not verified.</div>' : '')
        + candidates + (paths || '<div class="message">No verified path available.</div>')
        + '<div class="summary">' + (payload.warnings || []).map(n => pill(n, "warn")).join("") + '</div>';
    }

    function endpointSummary(title, endpoint) {
      return '<h3>' + title + '</h3><div class="summary">'
        + ["query", "device", "vrf", "interface", "ip", "mac"].filter(k => endpoint[k])
          .map(k => pill(k + ": " + endpoint[k])).join("") + '</div>';
    }

    function stepHtml(step, index) {
      const title = step.label || String(step.type || "step").replaceAll("_", " ");
      const fields = ["device", "vrf", "prefix", "lookup_ip", "next_hop", "interface", "protocol", "policy", "action", "status", "vsys", "from_zone", "to_zone", "original_to_zone", "security_rule", "nat_rule", "reason", "evidence_level", "ingress_vlan", "empty_fieldsets", "vtep", "vni", "from", "to", "local_interface", "remote_interface", "confirmation", "source", "freshness", "observed_at"];
      const details = fields.filter(key => step[key] != null && step[key] !== "")
        .map(key => pill(label(key) + ': ' + step[key])).join("");
      const evidence = Array.isArray(step.evidence) ? step.evidence.map(e =>
        typeof e === "object" ? pill((e.source || "observation") + ' · ' + (e.last_success_at || e.observed_at || "")) : "").join("") : "";
      let flowInfo = '';
      if (step.flow_before) flowInfo += '<div class="message">进入 / Before: ' + escapeHtml(flowText(step.flow_before)) + '</div>';
      if (step.flow_after) flowInfo += '<div class="message">' + (step.status === 'blocked' ? '候选 NAT（已拒绝） / Candidate NAT (denied): ' : '离开 / After: ') + escapeHtml(flowText(step.flow_after)) + '</div>';
      if (step.policy_resolution === 'action_only') flowInfo += '<div class="message">动作确定为允许；具体规则未确定 / Allow action established; exact rule unresolved</div>';
      if (step.security_candidates) flowInfo += tableSection('可能的首条匹配规则 / Possible first-match rules', step.security_candidates, ['name','source','destination','application','service','action','match'])
        + (step.security_candidates_truncated ? '<div class="message">仅显示前 100 条；判定已检查全部候选 / First 100 shown; all candidates evaluated</div>' : '');
      if (step.type === 'firewall_policy' && step.to_zone && step.original_to_zone && step.config_digest && step.verification_flow.protocol !== 'any') {
        const check = {device_id:step.device_id, flow:step.verification_flow, ingress:step.interface, vsys:step.vsys,
          from_zone:step.from_zone, to_zone:step.to_zone, original_to_zone:step.original_to_zone, config_digest:step.config_digest};
        const index = paChecks.push(check) - 1;
        flowInfo += '<button type="button" class="pa-verify" data-check="' + index + '">PA 原生规则/会话核验 / Verify on PA</button><div class="pa-verification-result"></div>';
      }
      return '<div class="step"><div class="step-title">' + (index + 1) + '. ' + escapeHtml(title) + '</div><div class="step-meta summary">' + details + '</div>'
        + flowInfo + (evidence ? '<details><summary>Evidence</summary><div class="summary">' + evidence + '</div></details>' : '') + '</div>';
    }

    function flowText(flow) {
      return (flow.protocol || 'any').toUpperCase() + ' ' + flow.source_ip + (flow.source_port != null ? ':' + flow.source_port : '')
        + ' → ' + flow.destination_ip + (flow.destination_port != null ? ':' + flow.destination_port : '')
        + (flow.application ? ' · App-ID assumption: ' + flow.application : '');
    }

    document.getElementById('pathResult').addEventListener('click', async event => {
      const button = event.target.closest('.pa-verify');
      if (!button) return;
      const request = paChecks[Number(button.dataset.check)], target = button.nextElementSibling;
      button.disabled = true; target.textContent = '正在核验 / Verifying…';
      try {
        const response = await fetch('/api/pa/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(request)});
        const result = await response.json();
        if (!response.ok) throw new Error(JSON.stringify(result.detail));
        target.innerHTML = '<div class="message">' + escapeHtml(result.status + ' · ' + (result.reason || result.observed_at || '')) + '</div>'
          + (result.security ? tableSection('PA security · ' + result.security.status, result.security.rules || [], ['name','action']) : '')
          + (result.nat ? tableSection('PA NAT · ' + result.nat.status, result.nat.rules || [], ['name','action']) : '')
          + (result.sessions ? '<div class="message">Session: ' + escapeHtml(result.sessions.status) + '</div>' + tableSection('Observed sessions',result.sessions.entries || [],['idx','id','source','dst','sport','dport','application','state']) : '')
          + '<div class="step-meta">规则匹配不等于实际流量通过；没有会话不等于拒绝。 / Rule matching is not proof of delivery; no session is not a deny.</div>';
      } catch (error) { target.textContent = '核验失败 / Verification failed: ' + String(error); }
      finally { button.disabled = false; }
    });

    function tableSection(title, rows, columns) {
      if (!rows.length) return '<div><h3>' + escapeHtml(title) + '</h3><div class="message">No matches</div></div>';
      const head = columns.map(col => '<th>' + escapeHtml(label(col)) + '</th>').join("");
      const body = rows.map(row => '<tr>' + columns.map(col => '<td>' + escapeHtml(String(row[col] ?? "")) + '</td>').join("") + '</tr>').join("");
      return '<div><h3>' + escapeHtml(title) + '</h3><div class="table-wrap"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div></div>';
    }

    function pill(text, cls) {
      return '<span class="pill ' + (cls || "") + '">' + escapeHtml(text) + '</span>';
    }

    function label(value) {
      return value.replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase());
    }

    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    }

    function isIp(value) {
      const parts = value.split(".");
      if (parts.length !== 4) return false;
      return parts.every(part => /^\d+$/.test(part) && Number(part) >= 0 && Number(part) <= 255);
    }

    function selectTool(name) {
      const chosen=name==='mac'?'mac':'path';
      for(const tool of ['path','mac']) {
        document.getElementById('panel-'+tool).hidden=chosen!==tool;
        const tab=document.getElementById('tab-'+tool);
        tab.setAttribute('aria-selected',String(chosen===tool));tab.tabIndex=chosen===tool?0:-1;
      }
    }
    for(const button of document.querySelectorAll('[data-tool]')) {
      button.onclick=()=>{selectTool(button.dataset.tool);history.replaceState(null,'','#'+button.dataset.tool)};
      button.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'path':e.key==='End'?'mac':button.dataset.tool==='path'?'mac':'path';document.getElementById('tab-'+next).click();document.getElementById('tab-'+next).focus()}};
    }
    for(const entry of document.querySelectorAll('[data-open-tool]')) {
      entry.onclick=event=>{event.preventDefault();const tool=entry.dataset.openTool;selectTool(tool);history.replaceState(null,'','#'+tool);document.getElementById('panel-'+tool).scrollIntoView({block:'start'});document.getElementById(tool==='mac'?'macQuery':'srcQuery').focus({preventScroll:true})};
    }
    window.addEventListener('hashchange',()=>selectTool(location.hash==='#mac'?'mac':'path'));
    document.getElementById('macQuery').addEventListener('keydown',e=>{if(e.key==='Enter'&&!document.getElementById('macButton').disabled)lookupMac()});

</script></body></html>'''

TOOLS_PAGE = TOOLS_PAGE.replace('</style>', PATH_GRAPH_STYLE + '</style>', 1).replace('<script>', '<script>' + PATH_GRAPH_SCRIPT, 1)
