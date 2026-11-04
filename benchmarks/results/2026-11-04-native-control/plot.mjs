#!/usr/bin/env node
// Render the retained native ratios with Vega; no browser or benchmark execution.
import { readFile, writeFile, access } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const vega = await import(require.resolve('vega'));
const { Resvg, initWasm } = require('@resvg/resvg-wasm');
const root = dirname(fileURLToPath(import.meta.url));
const summary = JSON.parse(await readFile(join(root, 'original-summary.json'), 'utf8'));
if (summary.correctness_gate.passed !== true || summary.build_integrity.passed !== true
    || summary.warmup_pairs < 1 || summary.measured_pairs < 5)
  throw Error('Qualified native control evidence is required');

const series = [
  { key: 'before_vs_stryker', label: 'Main control', color: '#83adff', legendX: 8 },
  { key: 'after_vs_stryker', label: 'Diagnostic PR control', color: '#ffbc78', legendX: 222 },
];
const targets = [
  { label: 'Wall', field: 'wall_seconds', target: .50, y: 156 },
  { label: 'CPU', field: 'cpu_seconds', target: .80, y: 240 },
  { label: 'RSS', field: 'peak_aggregate_rss_bytes', target: .80, y: 324 },
];
const points = targets.flatMap(metric => series.map((candidate, index) => {
  const value = summary.comparisons[candidate.key].metrics[metric.field];
  const [low, high] = value.bootstrap_95_percent;
  if (![low, value.median, high].every(Number.isFinite)
      || !(0 <= low && low <= value.median && value.median <= high && high <= 1.4))
    throw Error('Interval outside figure scale');
  return { ...candidate, low, high, median: value.median, y: metric.y + (index ? 12 : -12),
    text: `${value.median.toFixed(3)}  [${low.toFixed(3)}, ${high.toFixed(3)}]` };
}));
const text = (value, x, y, size, color = '#eef1f7', extra = {}) => ({
  type: 'text', encode: { enter: { text: { value }, x: { value: x }, y: { value: y },
    fontSize: { value: size }, fill: { value: color }, ...extra } },
});
const spec = {
  $schema: 'https://vega.github.io/schema/vega/v6.json',
  width: 950, height: 500, padding: 20, autosize: 'none', background: '#12151c',
  config: { font: 'sans-serif' },
  data: [{ name: 'points', values: points }, { name: 'targets', values: targets },
    { name: 'legend', values: series },
    { name: 'grid', values: [0, .25, .5, .75, 1, 1.25].map(value => ({ value })) }],
  scales: [{ name: 'x', type: 'linear', domain: [0, 1.4], range: [150, 650], zero: true, nice: false }],
  axes: [{ scale: 'x', orient: 'bottom', offset: -110, domain: false, ticks: false,
    values: [0, .25, .5, .75, 1, 1.25], format: '.2f', labelColor: '#b2bac8', labelFontSize: 12,
    title: 'Paired Oximut / Stryker ratio · lower is better', titleColor: '#b2bac8',
    titleFontSize: 13, titlePadding: 15 }],
  marks: [
    text('Native same-engine control', 0, 28, 26, '#eef1f7', { fontWeight: { value: 'bold' } }),
    text('Paired medians and bootstrap 95% confidence intervals', 0, 57, 15, '#b2bac8'),
    { type: 'symbol', from: { data: 'legend' }, encode: { enter: {
      x: { field: 'legendX' }, y: { value: 87 }, size: { value: 70 }, fill: { field: 'color' } } } },
    { type: 'text', from: { data: 'legend' }, encode: { enter: {
      x: { signal: 'datum.legendX + 15' }, y: { value: 92 }, text: { field: 'label' },
      fill: { field: 'color' }, fontSize: { value: 14 } } } },
    { type: 'rule', from: { data: 'grid' }, encode: { enter: {
      x: { scale: 'x', field: 'value' }, y: { value: 120 }, y2: { value: 365 },
      stroke: { value: '#303744' }, strokeWidth: { value: .7 } } } },
    { type: 'rule', from: { data: 'targets' }, encode: { enter: {
      x: { scale: 'x', field: 'target' }, y: { signal: 'datum.y - 28' }, y2: { signal: 'datum.y + 28' },
      stroke: { value: '#7dcf9b' }, strokeDash: { value: [4, 3] }, strokeWidth: { value: 1.5 } } } },
    { type: 'text', from: { data: 'targets' }, encode: { enter: {
      x: { value: 0 }, y: { signal: 'datum.y - 3' }, text: { field: 'label' },
      fill: { value: '#eef1f7' }, fontSize: { value: 19 }, fontWeight: { value: 'bold' } } } },
    { type: 'text', from: { data: 'targets' }, encode: { enter: {
      x: { value: 0 }, y: { signal: 'datum.y + 19' },
      text: { signal: "'Target ≤ ' + format(datum.target, '.2f')" },
      fill: { value: '#b2bac8' }, fontSize: { value: 12 } } } },
    { type: 'rule', from: { data: 'points' }, encode: { enter: {
      x: { scale: 'x', field: 'low' }, x2: { scale: 'x', field: 'high' }, y: { field: 'y' },
      stroke: { field: 'color' }, strokeWidth: { value: 3 } } } },
    ...['low', 'high'].map(field => ({ type: 'rule', from: { data: 'points' }, encode: { enter: {
      x: { scale: 'x', field }, y: { signal: 'datum.y - 5' }, y2: { signal: 'datum.y + 5' },
      stroke: { field: 'color' }, strokeWidth: { value: 2 } } } })),
    { type: 'symbol', from: { data: 'points' }, encode: { enter: {
      x: { scale: 'x', field: 'median' }, y: { field: 'y' }, size: { value: 70 }, fill: { field: 'color' } } } },
    { type: 'text', from: { data: 'points' }, encode: { enter: {
      x: { value: 680 }, y: { signal: 'datum.y + 5' }, text: { field: 'text' },
      fill: { field: 'color' }, fontSize: { value: 14 } } } },
    text('Ubuntu 24.04 x86_64 · 1 warmup + 5 pairs · run 41844675894', 0, 449, 12, '#b2bac8'),
    text('Exact workload and strict build integrity pass. All after/before CIs include 1; no optimization claim.', 0, 472, 12, '#b2bac8'),
    text('Dashed green: mission targets, superseding the legacy evaluator thresholds.', 0, 495, 12, '#b2bac8'),
  ],
};
const view = new vega.View(vega.parse(spec), { renderer: 'none' });
let svg;
try { svg = await view.toSVG(); } finally { view.finalize(); }
const fonts = [process.env.CHART_FONT, '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
  '/system/fonts/Roboto-Regular.ttf'].filter(Boolean);
let fontPath;
for (const path of fonts) {
  try { await access(path); fontPath = path; break; } catch { /* Try the next installed font. */ }
}
if (!fontPath) throw Error('Set CHART_FONT to a readable TrueType font');
const family = process.env.CHART_FONT_FAMILY || (fontPath.includes('DejaVu') ? 'DejaVu Sans' : 'Roboto');
await initWasm(await readFile(join(dirname(require.resolve('@resvg/resvg-wasm')), 'index_bg.wasm')));
const renderer = new Resvg(svg, { font: { fontBuffers: [await readFile(fontPath)],
  defaultFontFamily: family, sansSerifFamily: family }, fitTo: { mode: 'zoom', value: 2 } });
try {
  const image = renderer.render();
  try { await writeFile(join(root, 'native-control.png'), image.asPng()); }
  finally { image.free(); }
} finally { renderer.free(); }
await writeFile(join(root, 'native-control.svg'), svg);
await writeFile(join(root, 'native-control.vg.json'), JSON.stringify(spec, null, 2) + '\n');
console.log('Wrote native-control.png, native-control.svg and native-control.vg.json');
