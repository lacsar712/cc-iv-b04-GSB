<template>
  <main>
    <header class="topbar">
      <h1>光伏组串IV扫描台</h1>
      <nav v-if="session">
        <button :class="{ active: page === 'submit' }" @click="page = 'submit'">扫描报送</button>
        <button :class="{ active: page === 'weakest' }" @click="goWeakest">同箱最弱串</button>
      </nav>
      <div v-if="session" class="who">
        {{ session.username }}（{{ isWriter ? "扫描员" : "观察员·只读" }}）
        <button class="secondary" @click="logout">退出</button>
      </div>
    </header>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456；观察员 watcher / watch123456 只能看。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <!-- 扫描报送页 -->
    <div v-else-if="page === 'submit'">
      <section>
        <button class="secondary" @click="refresh">刷新列表</button>
      </section>
      <section v-if="isWriter">
        <label>汇流箱号</label>
        <input list="box-options" v-model="boxCode" placeholder="选择或输入箱号，例如 汇流箱A" />
        <datalist id="box-options">
          <option v-for="b in boxes" :key="b" :value="b"></option>
        </datalist>
        <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
        <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
        <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
        <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
        <button :disabled="loading" @click="submit">报送扫描</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
      <section v-else>
        <p class="err">观察员为只读账号，不能报送扫描数据。</p>
      </section>
      <section>
        <table>
          <thead>
            <tr><th>编号</th><th>汇流箱</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in logs" :key="row.id">
              <td>{{ row.id }}</td>
              <td>{{ row.box_code || "—" }}</td>
              <td>{{ row.string_code }}</td>
              <td>{{ row.voc_v }}</td>
              <td>{{ row.isc_a }}</td>
              <td>{{ row.fill_factor }}</td>
              <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待办结' : '已办结' }}</span></td>
              <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- 同箱最弱串专页 -->
    <div v-else>
      <section class="picker">
        <div class="field">
          <label>汇流箱号</label>
          <select v-model="weakBox" @change="recompute">
            <option value="" disabled>请选择箱号</option>
            <option v-for="b in boxes" :key="b" :value="b">{{ b }}</option>
          </select>
        </div>
        <div class="field">
          <label>钟段起点</label>
          <input type="datetime-local" step="1" v-model="winStart" @change="recompute" />
        </div>
        <div class="field">
          <label>截止钟点</label>
          <input type="datetime-local" step="1" v-model="winEnd" @change="recompute" />
        </div>
        <div class="field">
          <button :disabled="!weakBox || recomputing" @click="recompute">重算最弱串</button>
        </div>
        <p v-if="weakError" class="err">{{ weakError }}</p>
      </section>

      <section v-if="snapshot">
        <p v-if="snapshot.state === 'blocked'" class="err">
          箱内有组串最近一条读数尚未办结（表中橙色标记），本轮不判定最弱串，差值一律不填。
        </p>
        <p v-else-if="snapshot.state === 'empty'" class="err">
          所选钟段内该箱没有任何读数。
        </p>
        <table>
          <thead>
            <tr><th>组串</th><th>最近读数</th><th>状态</th><th>最近办结开路电压 V</th><th>对最高串差值 V</th><th>最弱标记</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in snapshot.rows" :key="r.string_code" :class="{ weakrow: r.is_weakest }">
              <td>{{ r.string_code }}</td>
              <td>#{{ r.scan_id }}</td>
              <td>
                <span class="tag" :class="r.status === 'done' ? 'ok' : 'pending'">
                  {{ r.status === "done" ? "已办结" : "未办结" }}
                </span>
              </td>
              <td>{{ r.status === "done" ? r.voc_v : "—" }}</td>
              <td>{{ r.delta_v === null || r.delta_v === undefined ? "—" : r.delta_v.toFixed(3) }}</td>
              <td>
                <span v-if="r.is_weakest" class="tag bad">最弱串</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="readonly">
        <h2>口径（只读）</h2>
        <template v-if="snapshot">
          <p>钟段：{{ fmt(snapshot.window_start) }} 起，至 {{ fmt(snapshot.window_end) }} 止，终点不计入。</p>
          <p>判定状态：
            <span v-if="snapshot.state === 'ok'" class="tag ok">已办结可判定</span>
            <span v-else-if="snapshot.state === 'blocked'" class="tag pending">有串未办结·暂缓</span>
            <span v-else class="tag pending">钟段内无读数</span>
          </p>
          <p v-if="snapshot.state === 'ok'">
            箱内最高开路电压 {{ snapshot.baseline_voc }} V；最弱串为
            <strong>{{ snapshot.weakest_string }}</strong>（{{ snapshot.weakest_voc }} V，掉得最狠）。
            差值由后端按已办结读数重算得出，页面不可填、改了也不作数。
          </p>
          <p>重算人：{{ snapshot.recomputed_by }}；重算钟点：{{ fmt(snapshot.recomputed_at) }}。截止钟点与重算结果在同一事务一次提交。</p>
        </template>
        <p v-else class="sub">先在上方选定汇流箱号与钟段，系统会立即重算。</p>
      </section>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const boxes = ref([]);
const page = ref("submit");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const boxCode = ref("");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const loading = ref(false);

const weakBox = ref("");
const winStart = ref("");
const winEnd = ref("");
const snapshot = ref(null);
const weakError = ref("");
const recomputing = ref(false);

let timer;
const isWriter = computed(() => session.value?.role === "writer");

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(iso) {
  return iso ? iso.replace("T", " ").replace(/\.\d+.*$/, "") : "—";
}
function localInput(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

async function refresh() {
  if (!session.value) return;
  const [logsRes, boxRes] = await Promise.all([
    fetch("/api/logs", { headers: headers() }),
    fetch("/api/boxes", { headers: headers() }),
  ]);
  if (logsRes.status === 401) { logout(); return; }
  if (logsRes.ok) logs.value = await logsRes.json();
  if (boxRes.ok) {
    const latest = await boxRes.json();
    if (latest.join("|") !== boxes.value.join("|")) boxes.value = latest;
  }
}

async function tick() {
  await refresh();
  // 停在“有串未办结”页时，等工人办结后自动重算一轮
  if (page.value === "weakest" && weakBox.value && snapshot.value?.state === "blocked") {
    await recompute();
  }
}

async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refresh();
    timer = setInterval(tick, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}

function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  boxes.value = [];
  snapshot.value = null;
  page.value = "submit";
  localStorage.removeItem("pv_session");
}

async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        box_code: boxCode.value,
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "报送失败"; return; }
    boxCode.value = stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch { error.value = "报送时网络异常"; }
  finally { loading.value = false; }
}

function goWeakest() {
  page.value = "weakest";
  if (!winEnd.value) {
    const end = new Date();
    const start = new Date(end.getTime() - 24 * 3600 * 1000);
    winEnd.value = localInput(end);
    winStart.value = localInput(start);
  }
  if (weakBox.value) recompute();
}

async function recompute() {
  if (!weakBox.value || !winStart.value || !winEnd.value) return;
  weakError.value = "";
  recomputing.value = true;
  try {
    // 只送箱号与钟段；差值、最弱标记由后端重算，前端从不提交
    const res = await fetch(`/api/boxes/${encodeURIComponent(weakBox.value)}/recompute`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        window_start: new Date(winStart.value).toISOString(),
        window_end: new Date(winEnd.value).toISOString(),
      }),
    });
    const data = await res.json();
    if (res.status === 401) { logout(); return; }
    if (!res.ok) { weakError.value = data.detail || "重算失败"; snapshot.value = null; return; }
    snapshot.value = data;
  } catch {
    weakError.value = "重算时网络异常";
  } finally {
    recomputing.value = false;
  }
}

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(tick, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1020px; margin: 0 auto; padding: 1.5rem; }
.topbar { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem; }
h1 { color: #86efac; margin: 0; font-size: 1.4rem; }
nav button { background: transparent; border: 1px solid #166534; color: #a7f3d0; }
nav button.active { background: #16a34a; color: #fff; border-color: #16a34a; }
.who { margin-left: auto; color: #a7f3d0; font-size: 0.9rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input, select { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary { background: #365314; }
.err { color: #fecaca; }
.picker { display: flex; gap: 1rem; align-items: flex-end; flex-wrap: wrap; }
.picker .field { flex: 1 1 180px; }
.picker .field button { margin-bottom: 0.75rem; }
.readonly { background: #0f3d24; border-style: dashed; }
.readonly h2 { margin: 0 0 0.5rem; font-size: 1rem; color: #86efac; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
tr.weakrow { background: #450a0a; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
