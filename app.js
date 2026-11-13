const $ = (id) => document.getElementById(id);
let datasetId = "",
  operator = null,
  receipt = null,
  busy = false,
  readSequence = 0;
const ratios = { half: [1, 2], three_quarters: [3, 4], nine_tenths: [9, 10] };
const rational = (x) =>
  x.numerator + (x.denominator === "1" ? "" : " / " + x.denominator);
async function api(path, body) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json();
  if (!response.ok) throw Error(data.error || "Request failed.");
  return data;
}
function controls() {
  for (const id of ["datasets", "example", "seal", "refresh"])
    $(id).disabled = busy;
  for (const id of ["release", "export-ledger"])
    $(id).disabled = busy || !datasetId;
  for (const id of ["download", "repeat"]) $(id).disabled = busy || !receipt;
  $("region").disabled = $("kind").value === "histogram";
}
function query() {
  return {
    kind: $("kind").value,
    region: $("kind").value === "histogram" ? null : $("region").value || null,
    level: $("level").value,
  };
}
function cost() {
  const [n, d] = ratios[$("level").value],
    q = n / d;
  $("cost-note").textContent =
    "New release: ε = " +
    Math.log(d / n).toFixed(3) +
    ", factor ×" +
    d +
    "/" +
    n +
    ". Noise standard deviation " +
    Math.sqrt((2 * q) / (1 - q) ** 2).toFixed(2) +
    " per count. Cached query: no additional cost.";
  controls();
}
async function loadList(preferred = datasetId) {
  const list = await api("/api/datasets");
  $("datasets").replaceChildren();
  if (!list.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "No sealed datasets";
    $("datasets").append(o);
    datasetId = "";
    controls();
    return;
  }
  for (const d of list) {
    const o = document.createElement("option");
    o.value = d.id;
    o.textContent = d.name + " · " + d.record_count + " records";
    $("datasets").append(o);
  }
  datasetId = list.some((d) => d.id === preferred) ? preferred : list[0].id;
  $("datasets").value = datasetId;
  await refresh();
}
async function refresh() {
  if (!datasetId) return;
  const seq = ++readSequence,
    id = datasetId;
  const data = await api("/api/datasets/" + id + "/operator");
  if (seq !== readSequence || id !== datasetId) return;
  operator = data;
  if (receipt?.release.dataset_id !== id) receipt = null;
  render();
}
function render() {
  if (!operator) return;
  const l = operator.ledger;
  $("spent").textContent = l.spent.epsilon.toFixed(3);
  $("remaining").textContent = l.remaining.epsilon.toFixed(3);
  $("total").textContent = l.budget_epsilon.toFixed(3);
  $("meter").value = Math.min(1, l.spent.epsilon / l.budget_epsilon);
  $("exact-budget").textContent =
    "Exact spent factor " +
    rational(l.spent) +
    " ≤ budget " +
    l.budget_factor +
    ". Remaining factor " +
    rational(l.remaining) +
    ".";
  $("audit").textContent = "Hash chain + budget verified";
  $("truth").replaceChildren();
  for (const [region, value] of Object.entries(operator.true_histogram)) {
    const row = document.createElement("div");
    const name = document.createElement("span"),
      count = document.createElement("strong");
    name.textContent = region;
    count.textContent = value;
    row.append(name, count);
    $("truth").append(row);
  }
  const total = document.createElement("p");
  total.textContent =
    "Exact total: " + operator.record_count + " · operator only";
  $("truth").append(total);
  if (!receipt && l.entries.length)
    receipt = { ...l.entries.at(-1), replayed: null };
  renderReceipt();
  renderLedger();
  controls();
}
function renderLedger() {
  $("ledger-rows").replaceChildren();
  if (!operator) return;
  for (const entry of operator.ledger.entries) {
    const p = entry.release,
      tr = document.createElement("tr");
    if (receipt?.release.sequence === p.sequence) tr.className = "selected";
    for (const text of [
      "#" + p.sequence,
      p.query.kind + (p.query.region ? " / " + p.query.region : ""),
      p.q.numerator + "/" + p.q.denominator,
      p.epsilon.toFixed(3),
      rational(p.after),
    ]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    const td = document.createElement("td"),
      button = document.createElement("button");
    button.textContent = "View";
    button.setAttribute("aria-label", "View release " + p.sequence);
    button.onclick = () => {
      receipt = { ...entry, replayed: null };
      renderReceipt();
      renderLedger();
      controls();
    };
    td.append(button);
    tr.append(td);
    $("ledger-rows").append(tr);
  }
  if (!operator.ledger.entries.length) {
    const tr = document.createElement("tr"),
      td = document.createElement("td");
    td.colSpan = 6;
    td.textContent = "No releases have left this source.";
    tr.append(td);
    $("ledger-rows").append(tr);
  }
}
function renderReceipt() {
  $("values").replaceChildren();
  if (!receipt) {
    $("receipt-title").textContent = "No release yet";
    $("receipt-kind").textContent = "—";
    $("receipt-note").textContent = "";
    const p = document.createElement("p");
    p.textContent =
      "A new query spends budget once. Repeating it returns the stored noisy result.";
    $("values").append(p);
    return;
  }
  const p = receipt.release;
  $("receipt-title").textContent =
    "Release " + p.sequence + " / " + p.query.kind;
  $("receipt-kind").textContent =
    receipt.replayed === true
      ? "CACHED · NO NEW COST"
      : receipt.replayed === false
        ? "NEW RELEASE"
        : "STORED RELEASE";
  const order = ["count", "north", "south", "east", "west"];
  for (const [name, value] of Object.entries(p.values).sort(
    (a, b) => order.indexOf(a[0]) - order.indexOf(b[0]),
  )) {
    const row = document.createElement("div");
    row.className = "value-row";
    const label = document.createElement("span"),
      number = document.createElement("strong");
    label.textContent =
      name === "count" ? p.query.region || "all regions" : name;
    number.textContent = value;
    row.append(label, number);
    $("values").append(row);
  }
  $("receipt-note").textContent =
    "Raw noisy integers. No clipping. Original release cost ε = " +
    p.epsilon.toFixed(3) +
    ". Record checksum " +
    receipt.hash.slice(0, 12) +
    "…";
}
async function release(q) {
  if (!datasetId || busy) return;
  busy = true;
  controls();
  $("error").textContent = "";
  const id = datasetId;
  try {
    const response = await api("/api/datasets/" + id + "/release", q);
    receipt = response;
    await refresh();
    $("status").textContent = response.replayed
      ? "Returned the identical cached release. Budget unchanged."
      : "Stored a new noisy release and its exact budget charge atomically.";
  } catch (e) {
    $("error").textContent = e.message;
    $("status").textContent = "No new release was returned.";
    try {
      await refresh();
    } catch {}
  } finally {
    busy = false;
    controls();
  }
}
$("query-form").onsubmit = (e) => {
  e.preventDefault();
  release(query());
};
$("repeat").onclick = () => {
  if (receipt) release(receipt.release.query);
};
for (const id of ["kind", "region", "level"]) $(id).onchange = cost;
$("datasets").onchange = async () => {
  datasetId = $("datasets").value;
  receipt = null;
  $("error").textContent = "";
  try {
    await refresh();
    $("status").textContent =
      "Loaded the sealed dataset and persisted releases.";
  } catch (e) {
    $("error").textContent = e.message;
  }
};
$("refresh").onclick = async () => {
  try {
    await loadList();
    $("status").textContent = "Re-read and verified the persisted ledger.";
    $("error").textContent = "";
  } catch (e) {
    $("error").textContent = e.message;
  }
};
async function seal(data) {
  if (busy) return;
  busy = true;
  controls();
  $("error").textContent = "";
  try {
    const response = await api("/api/datasets", data);
    receipt = null;
    await loadList(response.dataset_id);
    $("status").textContent = response.replayed
      ? "Identical records already sealed. Reusing the existing dataset and budget."
      : "Sealed immutable records. The budget cannot be reset for identical records.";
  } catch (e) {
    $("error").textContent = e.message;
  } finally {
    busy = false;
    controls();
  }
}
$("example").onclick = async () => {
  try {
    await seal(await api("/api/example"));
  } catch (e) {
    $("error").textContent = e.message;
  }
};
$("seal-form").onsubmit = (e) => {
  e.preventDefault();
  try {
    seal({
      name: $("name").value,
      budget_factor: Number($("budget").value),
      records: JSON.parse($("records").value),
    });
  } catch (e) {
    $("error").textContent = "Check the records JSON: " + e.message;
  }
};
function download(name, value) {
  const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    ),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("download").onclick = () => {
  if (receipt)
    download("noise-ledger-release-" + receipt.release.sequence + ".json", {
      release: receipt.release,
      hash: receipt.hash,
      canonical_payload: receipt.canonical_payload,
    });
};
$("export-ledger").onclick = () => {
  if (operator) download("noise-ledger-releases.json", operator.ledger);
};
cost();
loadList().catch((e) => {
  $("error").textContent = e.message;
});
