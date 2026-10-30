"""Self-contained, dependency-free device-visit visualization for the tools page."""

PATH_GRAPH_STYLE = r"""
    section, .result { min-width: 0; }
    .result:empty { display: none; }
    .path-results { grid-column: 1 / -1; }
    .path-branch { border: 1px solid #d7dee8; border-radius: 12px; overflow: hidden; margin-bottom: 16px; }
    .path-branch-header { padding: 16px 20px; background: #f8fafc; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }
    .path-branch-header h3 { margin: 0; font-size: 16px; }
    .path-branch-header label { display: flex; align-items: center; gap: 6px; margin: 0; }
    .path-branch-header input { width: 16px; height: 16px; }
    .path-graph-scroll { overflow: auto; background: #f8fafc; }
    .path-diagram { display: block; width: 100%; min-width: 760px; }
    .path-node { cursor: pointer; outline: none; }
    .path-node .node-card { fill: #fff; stroke: #94a3b8; stroke-width: 1.5; }
    .path-node:hover .node-card, .path-node:focus .node-card, .path-node[aria-pressed="true"] .node-card { stroke: #2563eb; stroke-width: 3; fill: #eff6ff; }
    .path-node .node-name { fill: #0f172a; font-size: 15px; font-weight: 700; }
    .path-node .node-badge { fill: #0369a1; font-size: 11px; }
    .path-node .node-port { fill: #475569; font-size: 11px; }
    .path-node .node-number { fill: #fff; font-size: 11px; font-weight: 700; }
    .path-node .number-disc { fill: #0369a1; }
    .path-node.revisit .node-card { stroke: #a78bfa; }
    .path-node.revisit .node-badge { fill: #7c3aed; }
    .path-node.arrived .node-card { stroke: #16a34a; }
    .path-node.blocked .node-card { stroke: #dc2626; fill: #fff1f2; }
    .path-node.incomplete .node-card { stroke: #d97706; fill: #fffbeb; }
    .path-edge { fill: none; stroke: #64748b; stroke-width: 2; }
    .path-edge.overlay { stroke: #0284c7; }
    .path-edge.gap { stroke: #d97706; stroke-dasharray: 5 4; }
    .path-edge-label { fill: #64748b; font-size: 9px; text-anchor: middle; }
    .hide-ports .node-port { visibility: hidden; }
    .path-graph-note { padding: 10px 20px; color: #64748b; font-size: 12px; border-top: 1px solid #e2e8f0; }
    .path-node-detail { margin: 12px 20px; padding: 12px; border-radius: 8px; background: #f1f5f9; }
    .path-node-detail:empty { display: none; }
    .path-evidence { margin: 16px 20px; }
    .path-evidence > summary { cursor: pointer; font-weight: 600; padding: 8px 0; }
    .path-branch > .message { margin: 12px 20px; }
"""

PATH_GRAPH_SCRIPT = r"""
    let pathGraphs = [];

    // Nodes represent visits, not unique device names: one-arm firewall returns
    // must retain both occurrences of the service switch.
    function pathVisits(steps) {
      const nodes = [], edges = [], counts = new Map();
      function visit(name) {
        if (!name) return null;
        let node = nodes[nodes.length - 1];
        if (!node || node.name !== name) {
          if (node) edges.push(null); // Evidence gap, never invent a physical link.
          const count = (counts.get(name) || 0) + 1;
          counts.set(name, count);
          node = {name, visit: count, steps: []};
          nodes.push(node);
        }
        return node;
      }
      steps.forEach((step, index) => {
        if (step.type === 'physical' && step.from && step.to) {
          visit(step.from).steps.push(index);
          // A physical self-edge is still a separate visit.
          const count = (counts.get(step.to) || 0) + 1;
          counts.set(step.to, count);
          edges.push(step);
          nodes.push({name: step.to, visit: count, steps: [index]});
        } else if (step.device) {
          visit(step.device).steps.push(index);
        }
      });
      nodes.forEach((node, index) => {
        node.input = edges[index - 1]?.remote_interface || '';
        node.output = edges[index]?.local_interface || '';
      });
      return {nodes, edges};
    }

    function pathGraphHtml(branch, branchIndex, payload) {
      const steps = branch.path || [];
      const graph = pathVisits(steps);
      pathGraphs[branchIndex] = {...graph, steps};
      if (!graph.nodes.length) return '<div class="message">暂无已确认的设备路径 / No observed device path.</div>';
      const columns = Math.min(5, graph.nodes.length), pitch = 226, rowPitch = 178;
      const cardWidth = 176, cardHeight = 114, width = columns * pitch + 22;
      const height = Math.ceil(graph.nodes.length / columns) * rowPitch + 8;
      const position = index => {
        const row = Math.floor(index / columns), col = index % columns;
        return {x: 36 + (row % 2 ? columns - col - 1 : col) * pitch, y: 28 + row * rowPitch};
      };
      const marker = 'path-arrow-' + branchIndex;
      let svg = '<svg class="path-diagram" viewBox="0 0 ' + width + ' ' + height + '" role="group" aria-label="设备路径图 / Device path">'
        + '<defs><marker id="' + marker + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker></defs>';
      graph.edges.forEach((edge, index) => {
        const a = position(index), b = position(index + 1);
        let x1, y1, x2, y2;
        if (a.y !== b.y) {
          x1 = x2 = a.x + cardWidth / 2; y1 = a.y + cardHeight; y2 = b.y - 5;
        } else {
          const right = b.x > a.x;
          x1 = a.x + (right ? cardWidth : 0); x2 = b.x + (right ? -5 : cardWidth + 5);
          y1 = y2 = a.y + cardHeight / 2;
        }
        const title = edge ? edge.from + ':' + edge.local_interface + ' → ' + edge.to + ':' + edge.remote_interface
          + (edge.vni ? ' · VNI ' + edge.vni + ' · VTEP ' + edge.vtep : '') : '连接证据缺失 / Connection evidence missing';
        svg += '<path class="path-edge ' + (!edge ? 'gap' : edge.vtep ? 'overlay' : '') + '" d="M ' + x1 + ' ' + y1 + ' L ' + x2 + ' ' + y2 + '" marker-end="url(#' + marker + ')"><title>' + escapeHtml(title) + '</title></path>';
        svg += '<text class="path-edge-label" x="' + ((x1 + x2) / 2 + (a.y !== b.y ? 28 : 0)) + '" y="' + ((y1 + y2) / 2 - 9) + '">' + (!edge ? '?' : edge.vtep ? 'VXLAN' : '物理') + '</text>';
      });
      graph.nodes.forEach((node, index) => {
        const p = position(index), last = index === graph.nodes.length - 1;
        const terminal = last ? (branch.status === 'ok' ? 'arrived' : branch.status === 'blocked' ? 'blocked' : 'incomplete') : '';
        const badge = (index === 0 ? '起点 / Source' : last && branch.status === 'ok' ? '终点 / Destination' : node.visit > 1 ? '再次经过 / Visit ' + node.visit : '途经 / Transit')
          + (last && branch.status !== 'ok' ? ' · ' + (branch.status === 'blocked' ? '阻断' : '未完成') : '');
        const title = node.name + ' · ' + badge + '\n' + node.steps.map(i => {
          const s = steps[i];
          return [s.type, s.interface, s.vrf, s.prefix, s.policy, s.name, s.action, s.vni ? 'VNI ' + s.vni : ''].filter(Boolean).join(' · ');
        }).join('\n');
        svg += '<g class="path-node ' + (node.visit > 1 ? 'revisit ' : '') + terminal + '" transform="translate(' + p.x + ',' + p.y + ')" role="button" tabindex="0" aria-pressed="false" aria-label="' + escapeHtml(node.name + ', visit ' + node.visit) + '" data-branch="' + branchIndex + '" data-visit="' + index + '">'
          + '<title>' + escapeHtml(title) + '</title><rect class="node-card" width="176" height="114" rx="12"/>'
          + '<circle class="number-disc" cx="0" cy="0" r="13"/><text class="node-number" x="0" y="4" text-anchor="middle">' + (index + 1) + '</text>'
          + '<text class="node-badge" x="14" y="23">' + escapeHtml(badge) + '</text>'
          + '<text class="node-name" x="14" y="47">' + escapeHtml(node.name.length > 18 ? node.name.slice(0, 17) + '…' : node.name) + '</text>'
          + '<text class="node-port" x="14" y="75">入 / In: ' + escapeHtml(node.input || '—') + '</text>'
          + '<text class="node-port" x="14" y="95">出 / Out: ' + escapeHtml(node.output || '—') + '</text></g>';
      });
      svg += '</svg>';
      return '<div class="path-graph-scroll">' + svg + '</div>'
        + '<div class="path-graph-note">按编号和箭头查看顺序；重复设备表示再次经过。点击设备查看本次经过的详情。 / Follow numbered visits; click a device for details.'
        + (payload.mode === 'physical' ? ' · 物理连通路径 / Physical connectivity' : ' · 基于采集数据推演 / Forwarding prediction') + '</div>'
        + '<div class="path-node-detail" id="path-node-detail-' + branchIndex + '" aria-live="polite"></div>';
    }

    function selectPathVisit(element) {
      const branch = Number(element.dataset.branch), index = Number(element.dataset.visit);
      const graph = pathGraphs[branch], node = graph?.nodes[index];
      if (!node) return;
      element.closest('.path-diagram').querySelectorAll('.path-node').forEach(n => n.setAttribute('aria-pressed', String(n === element)));
      document.getElementById('path-node-detail-' + branch).innerHTML = '<h3>' + escapeHtml(node.name) + ' · 第 ' + node.visit + ' 次经过 / Visit ' + node.visit + '</h3>'
        + '<div class="path">' + [...new Set(node.steps)].map(i => stepHtml(graph.steps[i], i)).join('') + '</div>';
    }
    document.getElementById('pathResult').addEventListener('click', event => {
      const node = event.target.closest('.path-node');
      if (node) selectPathVisit(node);
    });
    document.getElementById('pathResult').addEventListener('keydown', event => {
      if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.path-node')) {
        event.preventDefault(); selectPathVisit(event.target);
      }
    });
    document.getElementById('pathResult').addEventListener('change', event => {
      if (event.target.matches('.path-port-toggle')) event.target.closest('.path-branch').classList.toggle('hide-ports', !event.target.checked);
    });
"""
