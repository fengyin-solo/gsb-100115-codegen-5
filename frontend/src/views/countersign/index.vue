<template>
  <section class="page cs-page" data-module="countersign">
    <header class="page-head">
      <div>
        <h2>桥梁定检 · 检测记录会签桌</h2>
        <p class="page-desc">
          以桥面剖切图为主对象：检查员录入检测编号与桥梁名称、放置病害证据，
          主管选择限载结论后才生成工程任务。会签单向流转，撤回只能生成新一轮会签。
        </p>
      </div>
      <div class="page-actions cs-head-actions">
        <div class="role-switch">
          <span>当前身份</span>
          <button
            v-for="r in roles"
            :key="r"
            type="button"
            class="btn"
            :class="{ primary: role === r }"
            @click="role = r"
          >
            {{ r }}
          </button>
        </div>
        <button class="btn primary" type="button" @click="showOpen = true">开启新会签</button>
        <button class="btn" type="button" @click="showRecover = true">凭会签号恢复队列</button>
      </div>
    </header>

    <!-- 风险看板随结论更新 -->
    <div class="stat-row">
      <article v-for="card in board.cards" :key="card.label" class="stat-card" :class="riskCardClass(card.label)">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>
    <div class="risk-strip">
      <span class="risk-strip-title">风险分布</span>
      <span v-for="(v, k) in board.risk_counts" :key="k" class="risk-pill" :class="`risk-${riskKey(k)}`">
        {{ k }} {{ v }}
      </span>
      <span class="risk-strip-title">交通管制</span>
      <span v-for="(v, k) in board.control_types" :key="k" class="risk-pill">{{ k }} {{ v }}</span>
    </div>

    <div class="cs-grid">
      <!-- 左：会议列表 -->
      <aside class="cs-panel cs-list">
        <h3>会签会议</h3>
        <input v-model="keyword" class="cs-search" placeholder="按会签号 / 检测编号 / 桥名筛选" @keyup.enter="reloadList" />
        <ul class="meeting-list">
          <li
            v-for="m in meetings"
            :key="m.id"
            class="meeting-item"
            :class="{ active: current && current.id === m.id, withdrawn: m.status === '已撤回' }"
            @click="selectMeeting(m.id)"
          >
            <div class="meeting-top">
              <strong>{{ m.会签号 }}</strong>
              <span class="state-tag" :class="stateClass(m.status)">{{ m.status }}</span>
            </div>
            <div class="meeting-bridge">{{ m.桥梁名称 }}</div>
            <div class="meeting-meta">
              <span>{{ m.检测编号 }}</span>
              <span v-if="m.conclusion" class="risk-pill" :class="`risk-${riskKey(m.风险)}`">{{ m.conclusion }}</span>
            </div>
            <div v-if="m.supersedes" class="meeting-sup">替代 {{ m.supersedes }}</div>
          </li>
        </ul>
      </aside>

      <!-- 中：会签桌主工作区 -->
      <main class="cs-panel cs-main">
        <template v-if="current">
          <div class="meeting-header">
            <div>
              <h3>{{ current.桥梁名称 }} <small>{{ current.会签号 }}</small></h3>
              <div class="meeting-meta">
                <span>检测编号 {{ current.检测编号 }}</span>
                <span>检查员 {{ current.检查员 }}</span>
                <span v-if="current.supersedes" class="sup-badge">撤回 {{ current.supersedes }} 后新一轮会签</span>
              </div>
            </div>
            <span class="state-tag big" :class="stateClass(current.status)">{{ current.status }}</span>
          </div>

          <!-- 单向状态图 -->
          <ol class="state-track">
            <li v-for="(s, idx) in stateTrack" :key="s" class="track-step" :class="stepClass(idx)">
              <i>{{ idx + 1 }}</i>
              <span>{{ s }}</span>
            </li>
          </ol>

          <!-- 桥面剖切图 -->
          <div class="section-card">
            <div class="section-title">
              桥面剖切图（病害证据 {{ current.evidence.length }} 份）
              <small v-if="current.status === '待证据' && role === '检查员'">点击图面放置证据，再在下方补充病害类型</small>
              <small v-else>证据只能在检查员阶段放置</small>
            </div>
            <svg class="bridge-section" viewBox="0 0 400 220" @click="onSectionClick">
              <!-- 桥梁断面分层 -->
              <rect x="30" y="20" width="340" height="16" class="ly layer-pavement" />
              <text x="36" y="32" class="ly-label">桥面铺装</text>
              <rect x="30" y="36" width="340" height="10" class="ly layer-water" />
              <text x="36" y="45" class="ly-label">防水层</text>
              <rect x="30" y="46" width="340" height="26" class="ly layer-slab" />
              <text x="36" y="63" class="ly-label">桥面板</text>
              <rect x="30" y="72" width="340" height="50" class="ly layer-girder" />
              <!-- 主梁示意：空心板/箱室 -->
              <line x1="115" y1="72" x2="115" y2="122" class="girder-line" />
              <line x1="200" y1="72" x2="200" y2="122" class="girder-line" />
              <line x1="285" y1="72" x2="285" y2="122" class="girder-line" />
              <text x="36" y="102" class="ly-label">主梁（箱室）</text>
              <rect x="70" y="122" width="30" height="18" class="ly layer-bearing" />
              <rect x="300" y="122" width="30" height="18" class="ly layer-bearing" />
              <text x="36" y="136" class="ly-label">支座</text>
              <rect x="40" y="140" width="80" height="34" class="ly layer-pier" />
              <rect x="280" y="140" width="80" height="34" class="ly layer-pier" />
              <text x="126" y="162" class="ly-label">墩台盖梁</text>
              <!-- 地面线 -->
              <line x1="10" y1="186" x2="390" y2="186" class="ground-line" />
              <path d="M10 196 h8 M24 196 h8 M38 196 h8 M340 196 h8 M354 196 h8 M368 196 h8" class="ground-hatch" />

              <!-- 病害证据标记 -->
              <g v-for="(e, i) in current.evidence" :key="e.id">
                <circle
                  :cx="pctX(e.x)" :cy="pctY(e.y)" r="11"
                  class="evidence-pin"
                  :class="{ selected: selectedEvidence === i }"
                  @click.stop="selectedEvidence = i"
                />
                <text :x="pctX(e.x)" :y="pctY(e.y) + 4" class="pin-text" @click.stop="selectedEvidence = i">{{ i + 1 }}</text>
              </g>
            </svg>
            <div v-if="selectedEvidenceItem" class="evidence-detail">
              <strong>{{ selectedEvidenceItem.id }} · {{ selectedEvidenceItem.部位 }} · {{ selectedEvidenceItem.病害类型 }}</strong>
              <span>{{ selectedEvidenceItem.说明 || '无补充说明' }}</span>
              <small>放置人 {{ selectedEvidenceItem.placed_by }} · {{ selectedEvidenceItem.at }} · 照片 {{ selectedEvidenceItem.照片 }}</small>
            </div>
          </div>

          <!-- 检查员操作区 -->
          <section class="work-block" v-if="role === '检查员'">
            <h4>① 检查员：录入与病害证据</h4>
            <div class="form-grid">
              <label>专项评分<input v-model="form.special" type="number" min="0" max="100" :disabled="current.status !== '待证据'" /></label>
              <label>日常评分<input v-model="form.daily" type="number" min="0" max="100" :disabled="current.status !== '待证据'" /></label>
              <label>剖切图部位
                <select v-model="form.part" :disabled="current.status !== '待证据'">
                  <option v-for="l in layers" :key="l" :value="l">{{ l }}</option>
                </select>
              </label>
              <label>病害类型<input v-model="form.defect" placeholder="如：腹板斜裂缝" :disabled="current.status !== '待证据'" /></label>
              <label class="span-2">证据说明<input v-model="form.desc" placeholder="桩号、缝宽、面积等量化描述（先点剖切图取坐标）" :disabled="current.status !== '待证据'" /></label>
            </div>
            <div class="coord-hint">下一枚证据坐标：x {{ form.x.toFixed(0) }}% / y {{ form.y.toFixed(0) }}%（默认落在所选层位）</div>
            <div class="block-actions">
              <button class="btn" type="button" :disabled="current.status !== '待证据'" @click="placeEvidence">放置病害证据</button>
              <button
                class="btn primary" type="button"
                :disabled="current.status !== '待证据'"
                @click="doAction('检查员提交证据')"
              >
                提交证据，转主管结论
              </button>
            </div>
          </section>

          <!-- 主管操作区 -->
          <section class="work-block" v-else>
            <h4>② 主管：选择限载结论并签认</h4>
            <div v-if="current.status === '待结论'" class="conclusion-grid">
              <button
                v-for="c in conclusions"
                :key="c"
                type="button"
                class="conclusion-btn"
                :class="{ picked: form.conclusion === c, [`risk-${conclusionRisk[c]}`]: true }"
                @click="form.conclusion = c"
              >
                {{ c }}
                <small>{{ conclusionGrade[c] }} · 风险{{ conclusionRisk[c] }}</small>
              </button>
            </div>
            <label v-if="current.status === '待结论'" class="opinion-box">
              签认意见
              <textarea v-model="form.opinion" rows="2" placeholder="限载吨位、监测要求等（选填）"></textarea>
            </label>

            <div v-if="current.status === '待结论'" class="block-actions">
              <button class="btn primary" type="button" :disabled="!form.conclusion" @click="submitDecision(false)">
                主管签认（事务锁决定）
              </button>
              <button class="btn" type="button" :disabled="!form.conclusion" @click="submitDecision(true)">
                模拟多人同时签认（并发 5 份）
              </button>
            </div>

            <div v-if="current.status === '已会签'" class="decision-box">
              <p>
                会签结论已形成：<strong>{{ current.conclusion }}</strong>，
                评定 <strong>{{ current.评定等级 }}</strong>，风险
                <span class="risk-pill" :class="`risk-${riskKey(current.风险)}`">{{ current.风险 }}</span>
                决定人 {{ current.decision_by }}（{{ current.decided_at }}）
              </p>
              <p v-if="current.等级冲突" class="conflict-text">
                ⚠ 专项建议 {{ current.专项建议等级 }} 与日常评分 {{ current.日常评分等级 }} 分档冲突，
                以本次专项会签结论 {{ current.评定等级 }} 为准；既有桥位历史等级继续保留展示。
              </p>
              <div class="block-actions">
                <button class="btn primary" type="button" @click="doAction('生成工程任务')">③ 最后一步：生成工程任务</button>
                <button class="btn danger" type="button" @click="doAction('撤回并新会签')">撤回结论并新会签</button>
              </div>
            </div>

            <div v-if="current.status === '已派单'" class="decision-box">
              <p>工程任务已生成（工程清单 #{{ current.links.project_id }}），会签流程单向结束。</p>
              <div class="block-actions">
                <button class="btn danger" type="button" @click="doAction('撤回并新会签')">结论有变化？撤回并新会签</button>
              </div>
            </div>

            <p v-if="current.status === '待证据'" class="hint-text">检查员尚未提交病害证据，主管只能查看剖切图。</p>
          </section>

          <!-- 会签号恢复提示条 -->
          <section v-if="myToken" class="recover-bar">
            本机签认令牌 <code>{{ myToken }}</code>，会签号 <code>{{ current.会签号 }}</code>——
            连接中断后可用右上角「凭会签号恢复队列」取回结果
            <button class="link" type="button" @click="recoverMyToken">立即恢复我的签认</button>
          </section>
        </template>

        <div v-else class="empty-state cs-empty">从左侧选择一场会签，或开启新会签</div>

        <p v-if="message" class="cs-message" :class="{ error: !messageOk }">{{ message }}</p>
      </main>

      <!-- 右：同步落点 + 等待队列 + 流转历史 -->
      <aside class="cs-panel cs-side" v-if="current">
        <h3>会签联动</h3>
        <ul class="sync-list">
          <li>
            <span class="sync-name">定检档案</span>
            <span v-if="current.links.bridge_id" class="sync-state synced">#{{ current.links.bridge_id }} 已同步{{ current.status === '已派单' ? '并归档' : '' }}</span>
            <span v-else class="sync-state wait">待主管结论</span>
          </li>
          <li>
            <span class="sync-name">巡检通知</span>
            <span v-if="current.links.patrol_id" class="sync-state synced">#{{ current.links.patrol_id }} 已通知</span>
            <span v-else class="sync-state wait">待主管结论</span>
          </li>
          <li>
            <span class="sync-name">既有桥位</span>
            <span v-if="current.links.bridge_info_id" class="sync-state synced">#{{ current.links.bridge_info_id }} 历史等级保留</span>
            <span v-else class="sync-state wait">待关联桥位</span>
          </li>
          <li>
            <span class="sync-name">工程清单</span>
            <span v-if="current.links.project_id" class="sync-state synced">#{{ current.links.project_id }} 已派单</span>
            <span v-else-if="current.status === '已会签'" class="sync-state wait">结论已备，待派工</span>
            <span v-else class="sync-state wait">会签完成后生成</span>
          </li>
        </ul>

        <h3>并发签认队列</h3>
        <ul class="queue-list">
          <li v-for="q in current.queue" :key="q.token" class="queue-item" :class="queueClass(q.state)">
            <div><strong>{{ q.signer }}</strong><em>{{ q.state }}</em></div>
            <small>{{ q.结论 }} · {{ q.token.slice(0, 10) }}</small>
          </li>
          <li v-if="!current.queue.length" class="queue-empty">尚无主管签认请求</li>
        </ul>

        <h3>流转历史（单向）</h3>
        <ol class="history-list">
          <li v-for="(h, i) in [...current.history].reverse()" :key="i">
            <small>{{ h.at }}</small>
            <span>{{ h.事件 }}</span>
          </li>
        </ol>
      </aside>
    </div>

    <!-- 开启新会签 -->
    <div v-if="showOpen" class="modal-mask" @click.self="showOpen = false">
      <div class="modal">
        <h3>开启检测记录会签</h3>
        <p class="modal-hint">以桥面剖切图为主对象，会签号由服务端发放；先录入检测编号与桥梁名称。</p>
        <label>检测编号 *<input v-model="openForm.检测编号" placeholder="如 GJ-2026-042" /></label>
        <label>桥梁名称 *<input v-model="openForm.桥梁名称" placeholder="如 北京路跨线桥" /></label>
        <label>检查员<input v-model="openForm.检查员" placeholder="默认 值班检查员" /></label>
        <label>专项评分<input v-model="openForm.专项评分" type="number" min="0" max="100" /></label>
        <label>日常评分<input v-model="openForm.日常评分" type="number" min="0" max="100" /></label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="showOpen = false">取消</button>
          <button class="btn primary" type="button" @click="openMeeting">开启会签桌</button>
        </div>
      </div>
    </div>

    <!-- 凭会签号恢复队列 -->
    <div v-if="showRecover" class="modal-mask" @click.self="showRecover = false">
      <div class="modal">
        <h3>连接中断恢复</h3>
        <p class="modal-hint">凭会签号恢复等待队列；填写本人签认令牌可直接取回最终结果（中签/排队/未中签）。</p>
        <label>会签号 *<input v-model="recoverForm.no" placeholder="如 HQ-2026-0009" /></label>
        <label>签认令牌（选填）<input v-model="recoverForm.token" placeholder="断线前拿到的 token" /></label>
        <div v-if="recoverResult" class="recover-result">
          <p>{{ recoverResult.message }}</p>
          <p v-if="recoverResult.recovery?.state">
            我的签认：<em>{{ recoverResult.recovery.state }}</em>
            <span v-if="recoverResult.recovery.queue_position">，第 {{ recoverResult.recovery.queue_position }} 位</span>
            <span v-if="recoverResult.recovery.结论">，最终结论「{{ recoverResult.recovery.结论 }}」</span>
          </p>
        </div>
        <div class="modal-actions">
          <button class="btn" type="button" @click="showRecover = false">关闭</button>
          <button class="btn primary" type="button" @click="recoverQueue">恢复队列</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/countersign'
const roles = ['检查员', '主管'] as const
const stateTrack = ['待证据', '待结论', '已会签', '已派单']

type Role = (typeof roles)[number]
type QueueState = '放行决定' | '排队中' | '未中签'
interface Evidence { id: string; 部位: string; 病害类型: string; 说明: string; x: number; y: number; placed_by: string; at: string; 照片: string }
interface QueueItem { token: string; signer: string; 结论: string; state: QueueState }
interface Meeting {
  id: number; 会签号: string; 检测编号: string; 桥梁名称: string; 检查员: string
  专项评分: number | null; 日常评分: number | null
  status: string; conclusion: string | null; 限载结论: string | null
  评定等级: string | null; 专项建议等级: string | null; 日常评分等级: string | null
  等级冲突: boolean; 风险: string | null; decision_by: string | null; decided_at: string | null
  evidence: Evidence[]; queue: QueueItem[]; history: { at: string; 事件: string }[]
  links: Record<string, number>; supersedes: string | null
}

const role = ref<Role>('检查员')
const meetings = ref<Meeting[]>([])
const current = ref<Meeting | null>(null)
const selectedId = ref<number | null>(null)
const keyword = ref('')
const layers = ref<string[]>([])
const conclusions = ref<string[]>([])
const conclusionGrade = reactive<Record<string, string>>({})
const conclusionRisk = reactive<Record<string, string>>({})
const message = ref('')
const messageOk = ref(true)
const selectedEvidence = ref<number | null>(null)
const myToken = ref('')

const form = reactive({
  special: '' as string | number,
  daily: '' as string | number,
  part: '主梁',
  defect: '',
  desc: '',
  x: 50,
  y: 55,
  conclusion: '',
  opinion: '',
})

const showOpen = ref(false)
const openForm = reactive({ 检测编号: '', 桥梁名称: '', 检查员: '', 专项评分: '', 日常评分: '' })
const showRecover = ref(false)
const recoverForm = reactive({ no: '', token: '' })
const recoverResult = ref<{ message: string; recovery?: { state: string; queue_position?: number; 结论?: string } } | null>(null)

const board = ref<{
  cards: { label: string; value: number }[]
  risk_counts: Record<string, number>
  control_types: Record<string, number>
}>({ cards: [], risk_counts: {}, control_types: {} })

const selectedEvidenceItem = computed<Evidence | null>(() => {
  if (!current.value || selectedEvidence.value === null) return null
  return current.value.evidence[selectedEvidence.value] ?? null
})

function flash(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

function riskKey(risk: string | null | undefined): string {
  return { 低: 'low', 中: 'mid', 较高: 'high', 高: 'higher', 极高: 'top' }[risk ?? ''] ?? 'mid'
}

function stateClass(status: string): string {
  return {
    待证据: 'st-evidence', 待结论: 'st-wait', 已会签: 'st-signed', 已派单: 'st-done', 已撤回: 'st-withdraw',
  }[status] ?? ''
}

function riskCardClass(label: string): string {
  return label.includes('较高风险') || label.includes('管制') ? 'card-alert' : ''
}

function pctX(x: number): number { return 10 + (x / 100) * 380 }
function pctY(y: number): number { return (y / 100) * 200 }

function stepClass(idx: number): string {
  if (!current.value || current.value.status === '已撤回') return ''
  const now = stateTrack.indexOf(current.value.status)
  if (idx < now) return 'done'
  if (idx === now) return 'active'
  return ''
}

function queueClass(state: string): string {
  return { 放行决定: 'q-win', 排队中: 'q-wait', 未中签: 'q-lose' }[state] ?? ''
}

async function loadMeta() {
  const res = await request(`${ENDPOINT}/meta`)
  if (res.ok) {
    const data = await res.json()
    layers.value = data.layers
    conclusions.value = data.conclusions
    form.part = data.layers.includes('主梁') ? '主梁' : data.layers[0]
  }
}

async function reloadList() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  const res = await request(`${ENDPOINT}?${query.toString()}`)
  if (res.ok) {
    const data = await res.json()
    meetings.value = data.items ?? []
  }
}

async function reloadBoard() {
  const res = await request(`${ENDPOINT}/risk-board`)
  if (res.ok) board.value = await res.json()
}

async function refreshCurrent() {
  if (!current.value) return
  const res = await request(`${ENDPOINT}/${current.value.id}`)
  if (res.ok) {
    const data: Meeting = await res.json()
    const id = current.value.id
    current.value = data
    // 保持列表项状态徽标同步
    const item = meetings.value.find((m) => m.id === id)
    if (item) Object.assign(item, data)
  }
}

async function selectMeeting(id: number) {
  selectedId.value = id
  myToken.value = ''
  selectedEvidence.value = null
  const res = await request(`${ENDPOINT}/${id}`)
  if (res.ok) {
    current.value = await res.json()
    hydrateForm()
  }
}

function hydrateForm() {
  const m = current.value
  if (!m) return
  form.special = m.专项评分 ?? ''
  form.daily = m.日常评分 ?? ''
  form.conclusion = ''
  form.opinion = ''
  form.defect = ''
  form.desc = ''
}

function onSectionClick(event: MouseEvent) {
  if (!current.value || current.value.status !== '待证据' || role.value !== '检查员') return
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  const px = ((event.clientX - rect.left) / rect.width) * 400
  const py = ((event.clientY - rect.top) / rect.height) * 220
  form.x = Math.min(Math.max(((px - 10) / 380) * 100, 0), 100)
  form.y = Math.min(Math.max((py / 200) * 100, 0), 100)
  // 按纵向坐标回填层位
  const hit = [...layers.value].reverse().find((l) => form.y >= layerDefaultY(l))
  if (hit) form.part = hit
}

function layerDefaultY(layer: string): number {
  // 与 SVG 分层纵向区间对应（百分比），点击时按落点选层位
  return { 桥面铺装: 10, 防水层: 18, 桥面板: 23, 主梁: 36, 支座: 61, 墩台盖梁: 70 }[layer] ?? 0
}

async function openMeeting() {
  const values: Record<string, string> = { ...openForm }
  const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values }) })
  const data = await res.json()
  if (!res.ok || !data.ok) {
    flash(data.detail || data.message || '开启会签失败', false)
    return
  }
  showOpen.value = false
  Object.assign(openForm, { 检测编号: '', 桥梁名称: '', 检查员: '', 专项评分: '', 日常评分: '' })
  await reloadList()
  await selectMeeting(data.entry.id)
  role.value = '检查员'
  flash(data.message)
}

async function placeEvidence() {
  if (!current.value) return
  const values = {
    角色: role.value, 部位: form.part, 病害类型: form.defect, 说明: form.desc,
    x: Number(form.x.toFixed(1)), y: Number(form.y.toFixed(1)),
  }
  const res = await request(`${ENDPOINT}/${current.value.id}/evidence`, {
    method: 'POST', body: JSON.stringify({ values }),
  })
  const data = await res.json()
  flash(data.message, data.ok)
  if (data.ok) {
    form.defect = ''
    form.desc = ''
    await selectMeeting(current.value.id)
  }
}

async function doAction(action: string) {
  if (!current.value) return
  const res = await request(`${ENDPOINT}/${current.value.id}/actions`, {
    method: 'POST', body: JSON.stringify({ values: { action, 角色: role.value } }),
  })
  const data = await res.json()
  flash(data.message, data.ok)
  if (data.ok) {
    if (action === '撤回并新会签' && data.entry) {
      await reloadList()
      await selectMeeting(data.entry.id)
      role.value = '检查员'
    } else {
      await selectMeeting(current.value.id)
    }
    await reloadBoard()
  }
}

function makeToken(): string {
  return `WEB-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

async function submitDecision(parallel: boolean) {
  if (!current.value || !form.conclusion) return
  const id = current.value.id
  const base = {
    角色: '主管', 结论: form.conclusion, signer: `值班主管-${parallel ? '并发' : '签认'}`, 意见: form.opinion,
  }
  if (!parallel) {
    myToken.value = makeToken()
  }
  const calls = parallel
    ? ['正常通行', '限速通行', '限载通行', '停用交通管制', '封闭重建'].map((c, i) =>
        postDecision(id, { ...base, 结论: c, token: makeToken(), signer: `并发主管${i + 1}` }))
    : [postDecision(id, { ...base, token: myToken.value })]
  const results = await Promise.all(calls)
  const win = results.find((r) => r.decision?.state === '放行决定')
  const waiting = results.filter((r) => r.decision?.state === '排队中')
  if (win) flash(`✅ ${win.message}`, true)
  else if (waiting.length) flash(`⏳ ${waiting[0].message}（可凭会签号恢复结果）`)
  else flash(results[0]?.message ?? '签认未生效', false)
  await selectMeeting(id)
  await reloadBoard()
}

async function postDecision(id: number, values: Record<string, unknown>) {
  try {
    const res = await request(`${ENDPOINT}/${id}/decision`, { method: 'POST', body: JSON.stringify({ values }) })
    return await res.json()
  } catch {
    return { message: '网络中断：请凭会签号恢复队列', decision: null }
  }
}

async function recoverQueue() {
  const query = new URLSearchParams({ 会签号: recoverForm.no })
  if (recoverForm.token) query.set('token', recoverForm.token)
  try {
    const res = await request(`${ENDPOINT}/recover?${query.toString()}`)
    const data = await res.json()
    recoverResult.value = { message: data.message, recovery: data.recovery }
    if (data.meeting) {
      const hit = meetings.value.find((m) => m.会签号 === data.meeting.会签号)
      if (hit) await selectMeeting(hit.id)
    }
  } catch {
    recoverResult.value = { message: '恢复失败：会签号不存在或服务不可达' }
  }
}

async function recoverMyToken() {
  if (!current.value || !myToken.value) return
  recoverForm.no = current.value.会签号
  recoverForm.token = myToken.value
  showRecover.value = true
  await recoverQueue()
}

let timer: number | undefined
onMounted(async () => {
  // 结论到风险分档的映射，用于按钮提示
  const gradeMap: Record<string, string> = { 正常通行: '1类', 限速通行: '2类', 限载通行: '3类', 停用交通管制: '4类', 封闭重建: '5类' }
  const riskMap: Record<string, string> = { 正常通行: '低', 限速通行: '中', 限载通行: '较高', 停用交通管制: '高', 封闭重建: '极高' }
  for (const c of ['正常通行', '限速通行', '限载通行', '停用交通管制', '封闭重建']) {
    conclusionGrade[c] = gradeMap[c] ?? ''
    conclusionRisk[c] = riskMap[c] ?? ''
  }
  await loadMeta()
  await reloadList()
  await reloadBoard()
  timer = window.setInterval(() => {
    void reloadBoard()
    void refreshCurrent()
  }, 5000)
})

onBeforeUnmount(() => window.clearInterval(timer))
</script>

<style scoped>
.cs-head-actions { gap: 8px; align-items: center; }
.role-switch { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--muted); margin-right: 8px; }
.role-switch .btn { padding: 4px 10px; font-size: 12px; }

.risk-strip { display: flex; align-items: center; gap: 8px; margin: -4px 0 12px; flex-wrap: wrap; }
.risk-strip-title { font-size: 12px; color: var(--muted); }
.risk-pill { font-size: 12px; padding: 2px 8px; border-radius: 10px; background: #eef2f7; white-space: nowrap; }
.risk-low { background: #e7f6ec; color: #1a7f37; }
.risk-mid { background: #fdf3dd; color: #9a6700; }
.risk-high { background: #fde7d6; color: #bc4b0f; }
.risk-higher { background: #fbd9d0; color: #c2410c; }
.risk-top { background: #f7c9d2; color: #b42318; font-weight: 600; }
.card-alert { border-color: #f0b8b0; }

.cs-grid { display: grid; grid-template-columns: 260px minmax(420px, 1fr) 290px; gap: 12px; align-items: start; }
.cs-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.cs-panel h3 { margin: 0 0 8px; font-size: 14px; }

.cs-search { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; }
.meeting-list { list-style: none; margin: 0; padding: 0; max-height: 560px; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
.meeting-item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; cursor: pointer; }
.meeting-item:hover { border-color: var(--brand); }
.meeting-item.active { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand) inset; }
.meeting-item.withdrawn { opacity: 0.7; background: #fafafa; }
.meeting-top { display: flex; justify-content: space-between; align-items: center; }
.meeting-bridge { font-size: 13px; margin: 2px 0; }
.meeting-meta { display: flex; gap: 8px; font-size: 12px; color: var(--muted); align-items: center; flex-wrap: wrap; }
.meeting-sup { font-size: 11px; color: #b42318; margin-top: 2px; }

.state-tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: #eef2f7; }
.state-tag.big { font-size: 13px; padding: 3px 12px; }
.st-evidence { background: #e8eefc; color: #1f4e9c; }
.st-wait { background: #fdf3dd; color: #9a6700; }
.st-signed { background: #e7f6ec; color: #1a7f37; }
.st-done { background: #dcfce8; color: #15803d; font-weight: 600; }
.st-withdraw { background: #f1e3e5; color: #9b2226; }

.meeting-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
.meeting-header h3 { margin: 0; font-size: 16px; }
.meeting-header small { font-weight: 400; color: var(--muted); font-size: 12px; margin-left: 6px; }
.sup-badge { color: #b42318; font-size: 12px; }

.state-track { list-style: none; display: flex; margin: 0 0 12px; padding: 0; }
.track-step { flex: 1; text-align: center; position: relative; font-size: 12px; color: var(--muted); }
.track-step i {
  display: inline-flex; width: 22px; height: 22px; border-radius: 50%; background: #e5eaf1;
  font-style: normal; align-items: center; justify-content: center; margin-bottom: 2px;
}
.track-step:not(:last-child)::after {
  content: ''; position: absolute; top: 10px; left: 60%; width: 80%; height: 2px; background: #e5eaf1;
}
.track-step.done i { background: #9cc8ff; color: #fff; }
.track-step.done::after { background: #9cc8ff; }
.track-step.active i { background: var(--brand); color: #fff; }
.track-step.active { color: #1f2937; font-weight: 600; }

.section-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 12px; }
.section-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; display: flex; gap: 10px; align-items: baseline; }
.section-title small { font-weight: 400; color: var(--muted); }
.bridge-section { width: 100%; height: auto; background: #fbfdff; border: 1px dashed #cdd7e4; border-radius: 6px; cursor: crosshair; }
.ly { stroke: #9fb0c3; stroke-width: 1; }
.layer-pavement { fill: #6b7a90; }
.layer-water { fill: #4db6c4; }
.layer-slab { fill: #cfd9e6; }
.layer-girder { fill: #e6ecf4; }
.layer-bearing { fill: #8b6f47; }
.layer-pier { fill: #b7a48c; }
.ly-label { font-size: 9px; fill: #475569; }
.girder-line { stroke: #9fb0c3; stroke-width: 1; }
.ground-line { stroke: #64748b; stroke-width: 2; }
.ground-hatch { stroke: #64748b; stroke-width: 1.5; fill: none; }
.evidence-pin { fill: #d92d20; stroke: #fff; stroke-width: 2; cursor: pointer; }
.evidence-pin.selected { fill: #1f6feb; r: 13; }
.pin-text { fill: #fff; font-size: 11px; text-anchor: middle; pointer-events: none; font-weight: 700; }
.evidence-detail { margin-top: 8px; font-size: 12px; display: flex; flex-direction: column; gap: 2px; background: #f8fafc; padding: 6px 8px; border-radius: 6px; }
.evidence-detail small { color: var(--muted); }

.work-block { border-top: 1px dashed var(--border); padding-top: 10px; margin-top: 10px; }
.work-block h4 { margin: 0 0 8px; font-size: 13px; }
.form-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.form-grid label, .opinion-box, .modal label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 3px; }
.form-grid .span-2 { grid-column: span 2; }
.form-grid input, .form-grid select, .opinion-box textarea, .modal input {
  padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; color: #1f2937;
}
.coord-hint { font-size: 12px; color: var(--muted); margin: 6px 0; }
.block-actions { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn.danger { border-color: #f0b8b0; color: #b42318; }

.conclusion-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-bottom: 8px; }
.conclusion-btn {
  border: 1px solid var(--border); border-radius: 8px; background: #fff; padding: 10px 6px; cursor: pointer;
  display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 600;
}
.conclusion-btn small { font-weight: 400; color: var(--muted); font-size: 11px; }
.conclusion-btn.picked { outline: 2px solid var(--brand); border-color: var(--brand); }
.conclusion-btn.risk-top { background: #fbe9ee; }
.opinion-box { margin-bottom: 8px; }

.decision-box { background: #f8fafc; border-radius: 8px; padding: 10px; }
.decision-box p { margin: 4px 0; font-size: 13px; }
.conflict-text { color: #b42318; }
.hint-text { color: var(--muted); font-size: 13px; }

.recover-bar { margin-top: 10px; padding: 8px 10px; background: #fdf3dd; border-radius: 6px; font-size: 12px; }
.recover-bar code { background: #fff; padding: 1px 4px; border-radius: 4px; }

.cs-side h3 { margin-top: 12px; }
.cs-side h3:first-child { margin-top: 0; }
.sync-list, .queue-list { list-style: none; margin: 0 0 6px; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.sync-list li { display: flex; justify-content: space-between; font-size: 12px; align-items: center; }
.sync-state { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
.synced { background: #e7f6ec; color: #1a7f37; }
.wait { background: #eef2f7; color: var(--muted); }
.queue-item { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 12px; }
.queue-item div { display: flex; justify-content: space-between; }
.queue-item em { font-style: normal; font-size: 11px; padding: 0 6px; border-radius: 8px; }
.queue-item.q-win { border-color: #8fd3a6; background: #f0faf3; }
.queue-item.q-win em { background: #dcfce8; color: #15803d; }
.queue-item.q-wait { border-color: #e9d3a0; background: #fdf9ee; }
.queue-item.q-wait em { background: #fdf3dd; color: #9a6700; }
.queue-item.q-lose { opacity: 0.75; }
.queue-item.q-lose em { background: #f1e3e5; color: #9b2226; }
.queue-item small { color: var(--muted); }
.queue-empty { font-size: 12px; color: var(--muted); }
.history-list { margin: 0; padding-left: 18px; font-size: 12px; display: flex; flex-direction: column; gap: 6px; max-height: 240px; overflow: auto; }
.history-list small { display: block; color: var(--muted); }

.cs-empty { padding: 60px 0; }
.cs-message { margin-top: 10px; font-size: 13px; color: #1a7f37; }
.cs-message.error { color: #b42318; }

.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); display: flex; align-items: center; justify-content: center; z-index: 20; }
.modal { background: #fff; border-radius: 10px; padding: 18px 20px; width: 420px; display: flex; flex-direction: column; gap: 10px; }
.modal h3 { margin: 0; }
.modal-hint { font-size: 12px; color: var(--muted); margin: 0; }
.modal label input { margin-top: 3px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.recover-result { background: #f8fafc; border-radius: 6px; padding: 8px 10px; font-size: 13px; }
.recover-result em { font-style: normal; background: #e8eefc; padding: 1px 6px; border-radius: 8px; }
</style>
