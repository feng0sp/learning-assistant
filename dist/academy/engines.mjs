// Deterministic teaching models shared by local and public editions.
export { createNetworkScenario, probeNetwork, networkHints } from './network.mjs';

function integer(value, min, max, label) {
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${label}需为 ${min}～${max} 的整数。`);
  return value;
}

export function simulateSchedule(processes, {algorithm = 'rr', quantum = 2} = {}) {
  if (!Array.isArray(processes) || !processes.length || processes.length > 12) throw new Error('请输入 1～12 个进程。');
  if (!['fcfs', 'rr'].includes(algorithm)) throw new Error('调度算法无效。');
  if (algorithm === 'rr') integer(quantum, 1, 100, '时间片'); else quantum = null;
  const jobs = processes.map((p, index) => ({id: String(p.id ?? '').trim(),
    arrival: integer(p.arrival, 0, 1000, '到达时刻'), burst: integer(p.burst, 1, 100, '运行时长'),
    remaining: p.burst, order: index, first: null, completion: null}));
  if (jobs.some(p => !p.id || p.id.length > 20) || new Set(jobs.map(p => p.id)).size !== jobs.length) throw new Error('进程名称需非空、互不重复，最长 20 字。');
  const arrivals = [...jobs].sort((a, b) => a.arrival - b.arrival || a.order - b.order);
  const ready = [], frames = [];
  let cursor = 0, time = 0;
  const admit = () => { while (cursor < arrivals.length && arrivals[cursor].arrival <= time) ready.push(arrivals[cursor++]); };
  while (cursor < arrivals.length || ready.length) {
    admit();
    if (!ready.length) {
      const end = arrivals[cursor].arrival;
      frames.push({id: null, start: time, end, readyBefore: [], readyAfter: [], remaining: jobs.map(p => ({id: p.id, remaining: p.remaining}))});
      time = end;
      admit();
    }
    const readyBefore = ready.map(p => p.id);
    const job = ready.shift();
    if (job.first === null) job.first = time;
    const start = time, duration = algorithm === 'fcfs' ? job.remaining : Math.min(quantum, job.remaining);
    job.remaining -= duration;
    time += duration;
    // Boundary convention: arrivals at slice end enter before the preempted job.
    admit();
    if (job.remaining) ready.push(job); else job.completion = time;
    frames.push({id: job.id, start, end: time, readyBefore, readyAfter: ready.map(p => p.id),
      remaining: jobs.map(p => ({id: p.id, remaining: p.remaining}))});
  }
  const metrics = jobs.map(p => ({id: p.id, arrival: p.arrival, burst: p.burst, first: p.first,
    completion: p.completion, turnaround: p.completion - p.arrival,
    waiting: p.completion - p.arrival - p.burst, response: p.first - p.arrival}));
  return {algorithm, quantum, frames, metrics, averageWaiting: metrics.reduce((s, p) => s + p.waiting, 0) / jobs.length,
    averageTurnaround: metrics.reduce((s, p) => s + p.turnaround, 0) / jobs.length};
}

export function simulateCache(addresses, {lines = 4, blockSize = 4} = {}) {
  for (const [n, label] of [[lines, '行数'], [blockSize, '块大小']]) {
    integer(n, 1, 64, label);
    if (!Number.isInteger(Math.log2(n))) throw new Error(`${label}需为 2 的幂。`);
  }
  if (!Array.isArray(addresses) || !addresses.length || addresses.length > 64) throw new Error('请输入 1～64 个地址。');
  const table = Array.from({length: lines}, (_, index) => ({index, valid: false, tag: null, block: null}));
  let hits = 0;
  const frames = addresses.map(raw => {
    const address = integer(raw, 0, 65535, '16 位字节地址');
    const block = Math.floor(address / blockSize), index = block % lines, tag = Math.floor(block / lines), offset = address % blockSize;
    const row = table[index], hit = row.valid && row.tag === tag;
    const evictedBlock = !hit && row.valid ? row.block : null;
    if (hit) hits++;
    else Object.assign(row, {valid: true, tag, block});
    return {address, block, index, tag, offset, hit, evictedBlock, hits,
      table: table.map(r => ({...r})), binary: address.toString(2).padStart(16, '0')};
  });
  return {frames, hits, misses: addresses.length - hits, hitRate: hits / addresses.length,
    lines, blockSize, tagBits: 16 - Math.log2(lines) - Math.log2(blockSize),
    indexBits: Math.log2(lines), offsetBits: Math.log2(blockSize)};
}

export function simulateHash(operations, {size = 7} = {}) {
  integer(size, 3, 31, '槽位数');
  if (!Array.isArray(operations) || !operations.length || operations.length > 40) throw new Error('请输入 1～40 个插入或查找操作。');
  const table = Array(size).fill(null), frames = [], results = [];
  let count = 0;
  operations.forEach((operation, operationIndex) => {
    const key = integer(operation.key, 0, 9999, '键');
    if (!['insert', 'search'].includes(operation.kind)) throw new Error('仅支持插入和查找。');
    const home = key % size, path = [];
    let status = 'full';
    for (let step = 0; step < size; step++) {
      const index = (home + step) % size, value = table[index];
      const match = value === key, empty = value === null;
      path.push(index);
      if (match) status = operation.kind === 'search' ? 'found' : 'present';
      else if (empty) {
        if (operation.kind === 'insert') { table[index] = key; count++; status = 'inserted'; }
        else status = 'not-found';
      } else status = step === size - 1 ? (operation.kind === 'insert' ? 'full' : 'not-found') : 'probing';
      frames.push({operationIndex, kind: operation.kind, key, home, index, previous: value,
        path: [...path], probes: path.length, status, count, table: [...table]});
      if (match || empty || step === size - 1) break;
    }
    results.push({kind: operation.kind, key, status, probes: path.length, path});
  });
  return {frames, results, table, count, size, loadFactor: count / size};
}
