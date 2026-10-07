<template>
  <div>
    <!-- 上方：选箱号 + 截止钟点，二者捆在同一次重算报送里 -->
    <section class="bar">
      <label>箱号</label>
      <select v-model="selectedBox" :disabled="!boxes.length || loading">
        <option v-for="b in boxes" :key="b" :value="b">{{ b }}</option>
      </select>
      <template v-if="isWriter">
        <label>截止钟点</label>
        <input type="datetime-local" v-model="cutoff" :disabled="loading" />
        <button :disabled="loading || !selectedBox || !cutoff" @click="recalc">重算并报送</button>
      </template>
      <span v-else class="hint">观察员不能报送，以下展示扫描员最近一次报送结果</span>
      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="hint" class="hint">{{ hint }}</p>
    </section>

    <!-- 中间：各串最近办结开路电压 + 最弱标记（均来自重算结果，不在画面上偷填） -->
    <section v-if="report">
      <p class="meta">
        箱号 {{ report.box_no }} ｜ 截止钟点 {{ fmt(report.cutoff_at) }} ｜
        报送人 {{ report.created_by }} ｜ 报送时间 {{ fmt(report.created_at) }}
      </p>
      <p v-if="report.complete && report.weakest_string" class="banner ok">
        最弱串：{{ report.weakest_string }}（最近办结 {{ report.weakest_voc }} V，
        差值 {{ report.weakest_drop }} V，基准 {{ report.reference_voc }} V）
      </p>
      <p v-else-if="!report.complete" class="banner warn">
        箱内还有 {{ report.pending_count }} 条读数未办结，按口径本次不出最弱标记
      </p>
      <p v-else class="banner warn">截止钟点前该箱暂无已办结读数，本次不出最弱标记</p>
      <table>
        <thead>
          <tr><th>组串</th><th>最近办结开路电压(V)</th><th>读数时刻</th><th>差值(V)</th><th>最弱标记</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in report.rows" :key="r.string_code">
            <td>{{ r.string_code }}</td>
            <td>{{ r.voc_v }}</td>
            <td>{{ fmt(r.scanned_at) }}</td>
            <td>{{ r.drop == null ? "—" : r.drop }}</td>
            <td><span v-if="r.is_weakest" class="tag bad">最弱</span><span v-else>—</span></td>
          </tr>
          <tr v-if="!report.rows.length"><td colspan="5">该箱在所选钟段内没有已办结读数</td></tr>
        </tbody>
      </table>
    </section>

    <!-- 下方：口径，只读 -->
    <section>
      <h2>口径（只读）</h2>
      <ol class="spec">
        <li>统计范围：所选箱号内、读数时刻不晚于截止钟点的扫描；落在所选钟段外的读数本次不算。</li>
        <li>每串取值：截止钟点之前最近一次办结（已完成）扫描的开路电压。</li>
        <li>差值口径：基准取箱内各串最近办结开路电压的最高值，差值 = 基准 − 该串电压；差值最大的一串记为最弱串。</li>
        <li>未办结约束：箱内任一读数尚未办结时，本次不出最弱标记，不填假数。</li>
        <li>报送约束：截止钟点与重算须捆在同一次提交里，少一边都不行；观察员不能报送。</li>
      </ol>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";

const props = defineProps({ session: { type: Object, required: true } });
const emit = defineEmits(["expired"]);

const isWriter = computed(() => props.session.role === "writer");
const boxes = ref([]);
const selectedBox = ref("");
const cutoff = ref(nowLocal());
const report = ref(null);
const error = ref("");
const hint = ref("");
const loading = ref(false);

function nowLocal() {
  const d = new Date();
  d.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fmt(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}
async function api(url, opts) {
  const res = await fetch(url, {
    ...opts,
    headers: { ...(opts && opts.headers), Authorization: "Bearer " + props.session.token },
  });
  if (res.status === 401) {
    emit("expired");
    throw new Error("expired");
  }
  return res;
}
async function loadBoxes() {
  try {
    const res = await api("/api/boxes");
    if (!res.ok) return;
    boxes.value = await res.json();
    if (boxes.value.length && !selectedBox.value) {
      selectedBox.value = boxes.value[0];
    } else if (!boxes.value.length) {
      hint.value = "暂无箱号：请先在「扫描记录」页提交带箱号的扫描。";
    }
  } catch (e) {
    if (e.message !== "expired") error.value = "无法连接接口";
  }
}
// 选定箱 → 连到重算 → 连到最弱标记；差值只认重算返回，不在画面上偷填
async function recalc() {
  if (!selectedBox.value || !cutoff.value) {
    report.value = null;
    hint.value = "箱号与截止钟点都要选齐，二者捆在同一次提交里。";
    return;
  }
  error.value = "";
  hint.value = "";
  loading.value = true;
  try {
    const res = await api("/api/box-weakest/recalc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        box_no: selectedBox.value,
        cutoff: new Date(cutoff.value).toISOString(),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      error.value = data.detail || "重算失败";
      return;
    }
    report.value = data;
  } catch (e) {
    if (e.message !== "expired") error.value = "无法连接接口";
  } finally {
    loading.value = false;
  }
}
async function fetchLatest() {
  if (!selectedBox.value) {
    report.value = null;
    return;
  }
  error.value = "";
  hint.value = "";
  loading.value = true;
  try {
    const res = await api("/api/box-weakest/latest?box_no=" + encodeURIComponent(selectedBox.value));
    if (res.status === 404) {
      report.value = null;
      hint.value = "该箱暂无报送结果。";
      return;
    }
    if (res.ok) report.value = await res.json();
  } catch (e) {
    if (e.message !== "expired") error.value = "无法连接接口";
  } finally {
    loading.value = false;
  }
}
// 换箱：扫描员连带触发重算，观察员只读已报送结果
watch(selectedBox, () => (isWriter.value ? recalc() : fetchLatest()));
// 改截止钟点同样连带重算，截止钟点与重算始终同次提交
watch(cutoff, () => {
  if (isWriter.value) recalc();
});
onMounted(loadBoxes);
</script>

<style scoped>
h2 { font-size: 1rem; color: #86efac; margin: 0 0 0.5rem; }
.bar label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
select, .bar input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
.meta { color: #a7f3d0; font-size: 0.85rem; }
.banner { padding: 0.5rem 0.75rem; border-radius: 6px; font-weight: 600; }
.banner.ok { background: #14532d; color: #bbf7d0; }
.banner.warn { background: #854d0e; color: #fde68a; }
.hint { color: #a7f3d0; font-size: 0.85rem; }
.err { color: #fecaca; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.tag.bad { background: #7f1d1d; color: #fecaca; }
.spec { margin: 0; padding-left: 1.25rem; color: #d1fae5; font-size: 0.9rem; line-height: 1.7; }
</style>
