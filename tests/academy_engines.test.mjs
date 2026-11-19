import test from 'node:test';
import assert from 'node:assert/strict';
import {simulateSchedule, simulateCache, simulateHash, createNetworkScenario, probeNetwork} from '../dist/academy/engines.mjs';

test('RR hand-calculated example, conservation, waiting and response', () => {
  const r = simulateSchedule([{id: 'A', arrival: 0, burst: 5}, {id: 'B', arrival: 0, burst: 3}], {quantum: 2});
  assert.deepEqual(r.frames.map(f => [f.id, f.start, f.end]), [['A',0,2], ['B',2,4], ['A',4,6], ['B',6,7], ['A',7,8]]);
  assert.deepEqual(r.metrics.map(m => [m.completion, m.waiting, m.response]), [[8,3,0],[7,4,2]]);
  assert.equal(r.averageWaiting, 3.5);
});
test('RR admits a boundary arrival before requeuing the running process', () => {
  const r = simulateSchedule([{id:'A',arrival:0,burst:4},{id:'B',arrival:2,burst:1}], {quantum:2});
  assert.deepEqual(r.frames.map(f => f.id), ['A','B','A']);
});
test('FCFS records idle periods and never starts before arrival', () => {
  const r = simulateSchedule([{id:'A',arrival:3,burst:2},{id:'B',arrival:8,burst:1}], {algorithm:'fcfs'});
  assert.deepEqual(r.frames.map(f => [f.id,f.start,f.end]), [[null,0,3],['A',3,5],[null,5,8],['B',8,9]]);
  assert.ok(r.metrics.every(m => m.waiting === 0));
});
test('one process, stable ties, invalid scheduling inputs', () => {
  assert.equal(simulateSchedule([{id:'P',arrival:0,burst:1}], {quantum:100}).metrics[0].completion, 1);
  assert.throws(() => simulateSchedule([{id:'P',arrival:0,burst:0}]));
  assert.throws(() => simulateSchedule([{id:'P',arrival:0,burst:1}], {quantum:0}));
  assert.throws(() => simulateSchedule([{id:'P',arrival:0,burst:1},{id:'P',arrival:0,burst:1}]));
});
test('scheduling workload properties across varied inputs', () => {
  for (let seed=1;seed<=30;seed++) for (const algorithm of ['rr','fcfs']) {
    const jobs=Array.from({length:5},(_,i)=>({id:`P${i}`,arrival:(seed*(i+1))%9,burst:1+(seed+i*3)%7}));
    const r=simulateSchedule(jobs,{algorithm,quantum:1+seed%4});
    for(const p of jobs) {
      assert.equal(r.frames.filter(f=>f.id===p.id).reduce((s,f)=>s+f.end-f.start,0),p.burst);
      assert.ok(r.frames.filter(f=>f.id===p.id).every(f=>f.start>=p.arrival));
    }
    assert.ok(r.metrics.every(m=>m.waiting>=0&&m.response>=0));
    assert.ok(r.frames.every((f,i)=>f.end>f.start&&(i===0||f.start===r.frames[i-1].end)));
  }
});
test('cache cold miss, locality, conflict and manual hit rate', () => {
  const r=simulateCache([0,1,16,0]);
  assert.deepEqual(r.frames.map(f=>f.hit),[false,true,false,false]);
  assert.deepEqual(r.frames.map(f=>[f.index,f.tag]),[[0,0],[0,0],[0,1],[0,0]]);
  assert.equal(r.hitRate,0.25);
  assert.equal(r.frames[2].evictedBlock,0);
  assert.equal(r.frames[0].table[0].tag,0); // earlier snapshots remain immutable
});
test('cache block boundaries and one-line cache', () => {
  assert.deepEqual(simulateCache([3,4,7],{lines:1,blockSize:4}).frames.map(f=>f.hit),[false,false,true]);
  assert.equal(simulateCache([65535],{lines:4,blockSize:4}).frames[0].offset,3);
  assert.throws(()=>simulateCache([-1]));
  assert.throws(()=>simulateCache([0],{lines:3}));
  assert.throws(()=>simulateCache([65536]));
});
test('hash collisions, wrap-around, duplicate insert and unsuccessful lookup', () => {
  const r=simulateHash([{kind:'insert',key:6},{kind:'insert',key:13},{kind:'insert',key:20},
    {kind:'search',key:20},{kind:'insert',key:13},{kind:'search',key:27}],{size:7});
  assert.deepEqual(r.table,[13,20,null,null,null,null,6]);
  assert.deepEqual(r.results.map(x=>x.status),['inserted','inserted','inserted','found','present','not-found']);
  assert.deepEqual(r.results[3].path,[6,0,1]);
  assert.equal(r.count,3);
});
test('full hash tables terminate within size probes', () => {
  const r=simulateHash([0,1,2].map(key=>({kind:'insert',key})).concat([{kind:'insert',key:3},{kind:'search',key:4}]),{size:3});
  assert.deepEqual(r.results.slice(-2).map(x=>[x.status,x.probes]),[['full',3],['not-found',3]]);
  assert.equal(r.table.length,3);
  assert.throws(()=>simulateHash([{kind:'insert',key:-1}]));
});
for(const kind of ['mask','gateway','dns','route','transfer']) test(`network ${kind}: reproducible faulty initial state and valid repair`,()=>{
  for(const seed of [1,2,3,4,17,200,999999]) {
    const s=createNetworkScenario(kind,seed);
    assert.equal(probeNetwork(s,s.config,'web').ok,false);
    const repaired=probeNetwork(s,s.solution,'web');
    assert.equal(repaired.ok,true,JSON.stringify(repaired));
    assert.ok(repaired.reply.length>=3);
    assert.deepEqual(s,createNetworkScenario(kind,seed));
  }
});
test('DNS fault preserves IP reachability and diagnosis distinguishes the failure',()=>{
  const s=createNetworkScenario('dns');
  assert.equal(probeNetwork(s,s.config,'server').ok,true);
  assert.equal(probeNetwork(s,s.config,'dns').ok,false);
  assert.equal(probeNetwork(s,s.config,'gateway').ok,true);
});
test('network validates return path, longest-prefix match and invalid inputs',()=>{
  const s=createNetworkScenario('route');
  const repaired=structuredClone(s.solution);
  s.nodes.find(n=>n.id==='r2').routes=[];
  assert.equal(probeNetwork(s,repaired,'server').ok,false);
  assert.match(probeNetwork(s,repaired,'server').message,/回复路径/);
  const other=createNetworkScenario('mask');
  const r1=other.nodes.find(n=>n.id==='r1');
  r1.routes=[{destination:'0.0.0.0/0',nextHop:'1.2.3.4',enabled:true}];
  // configured R1 task route wins; R2 also prefers its connected /24 over /0.
  other.nodes.find(n=>n.id==='r2').routes.push({destination:'0.0.0.0/0',nextHop:'1.2.3.4',enabled:true});
  assert.equal(probeNetwork(other,other.solution,'web').ok,true);
  assert.throws(()=>probeNetwork(other,{...other.solution,gateway:'999.1.1.1'},'gateway'));
  assert.throws(()=>createNetworkScenario('mask',0));
});
