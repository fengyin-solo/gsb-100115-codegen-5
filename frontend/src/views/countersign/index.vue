<template>
  <section class="page csd-page">
    <header class="page-head">
      <div>
        <h2>检测记录会签桌</h2>
        <p class="page-desc">
          以桥面剖切图为主对象：检查员录入检测编号与桥梁名称、放置病害证据，主管选择限载结论，
          最后生成工程任务；结果同步定检档案、巡检通知与工程清单。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">新建会签会议</button>
      </div>
    </header>

    <!-- 风险看板：随限载结论更新 -->
    <div class="risk-board">
      <div class="risk-col risk-summary">
        <h3>风险看板</h3>
        <div class="risk-pills">
          <span
            v-for="r in riskPills"
            :key="r.label"
            class="risk-pill"
            :class="`risk-${riskClass(r.label)}`"
          >
            {{ r.label }}风险 <b>{{ r.value }}</b>
          </span>
        </div>
        <div class="risk-conclusions">
          <span v-for="(count, name) in board.结论分布" :key="name" class="conclusion-chip">
            {{ name }} <b>{{ count }}</b>
          </span>
        </div>
      </div>
      <div class="risk-col risk-bridges">
        <h3>高风险桥位</h3>
        <ul v-if="board.高风险桥位?.length" class="risk-bridge-list">
          <li v-for="item in board.高风险桥位" :key="item.会签号" :class="`risk-text-${riskClass(item.风险等级)}`">
            <span class="dot" :class="`risk-${riskClass(item.风险等级)}`"></span>
            {{ item.桥梁名称 }}
            <em>{{ item.限载结论 }} · {{ item.技术等级 }} · {{ item.会签号 }}</em>
            <i v-if="item.评分冲突" class="conflict-flag">专项覆盖日常</i>
            <i v-if="item.任务编号" class="task-flag">{{ item.任务编号 }}</i>
          </li>
        </ul>
        <p v-else class="muted-text">暂无高风险桥位</p>
      </div>
      <div class="risk-col risk-queue">
        <h3>待主管会签 / 队列</h3>
        <p v-if="board.待主管会签?.length" class="muted-text">
          {{ board.待主管会签.length }} 场会议在等主管结论
        </p>
        <p class="queue-line">未放行签认：<b>{{ board.未放行签认 }}</b> 份（只留档不生效）</p>
        <p class="queue-line">已撤回会议：<b>{{ board.已撤回会议 }}</b> 场（撤回不回退，另开新届次）</p>
        <div class="recover-box">
          <input v-model="recoverCode" placeholder="凭会签号恢复队列，如 CSD-2026-0005-R2" />
          <button class="btn" type="button" @click="recoverQueue">恢复</button>
        </div>
      </div>
    </div>

    <div class="csd-layout">
      <!-- 左：会议列表 -->
      <aside class="meeting-list">
        <form class="meeting-filter" @submit.prevent="reload()">
          <input v-model="filters.keyword" placeholder="会签号 / 检测编号" />
          <select v-model="filters.status">
            <option value="">全部状态</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
          <input v-model="filters.bridge" placeholder="桥梁名称 / 桥位编号" />
          <button class="btn" type="submit">查询</button>
        </form>
        <ul>
          <li
            v-for="m in meetings"
            :key="m.id"
            class="meeting-item"
            :class="{ active: current && current.id === m.id, withdrawn: m.status === '已撤回' }"
            @click="select(m.id)"
          >
            <div class="meeting-item-head">
              <strong>{{ m.会议全称 }}</strong>
              <span class="status-tag" :class="`st-${statusClass(m.status)}`">{{ m.status }}</span>
            </div>
            <div class="meeting-item-body">{{ m.桥梁名称 }} · {{ m.检测编号 }}</div>
            <div class="meeting-item-foot">
              <span v-if="m.限载结论" :class="`risk-text-${riskClass(m.风险等级)}`">{{ m.限载结论 }} · {{ m.技术等级 }}</span>
              <span v-else class="muted-text">{{ m.病害证据?.length || 0 }} 处证据</span>
              <i v-if="m.评分冲突" class="conflict-flag">评分冲突</i>
            </div>
          </li>
        </ul>
      </aside>

      <!-- 右：会签工作台 -->
      <main class="workbench" v-if="current">
        <div class="wb-head">
          <div>
            <h3>{{ current.会议全称 }}
              <small v-if="current.届次 > 1">第 {{ current.届次 }} 届（承接 R{{ current.届次 - 1 }}，撤回后续开）</small>
            </h3>
            <p class="muted-text">
              {{ current.检测编号 }} · {{ current.桥梁名称 }}
              <template v-if="current.桥位编号"> · 桥位 {{ current.桥位编号 }}</template>
              · 检查员 {{ current.检查员 }}
              <template v-if="current.主管"> · 主管 {{ current.主管 }}</template>
            </p>
          </div>
          <div class="wb-head-actions">
            <button
              v-if="canWithdraw"
              class="btn danger"
              type="button"
              @click="withdraw"
            >撤回并开新届次</button>
          </div>
        </div>

        <!-- 单向状态图 -->
        <ol class="state-track">
          <li v-for="(s, i) in stateSteps" :key="s" class="state-step" :class="stepClass(i)">
            <span class="state-dot">{{ i + 1 }}</span>
            <span class="state-label">{{ s }}</span>
          </li>
        </ol>

        <div class="wb-grid">
          <!-- 桥面剖切图（主对象） -->
          <section class="panel deck-panel">
            <div class="panel-head">
              <h4>桥面剖切图</h4>
              <span class="muted-text">点击图上构件放置病害证据（{{ current.病害证据?.length || 0 }} 处）</span>
            </div>
            <div class="deck-canvas" :class="{ locked: !isDraft }">
              <svg viewBox="0 0 100 58" preserveAspectRatio="none" @click="onDeckClick">
                <!-- 桥面铺装 -->
                <rect x="2" y="10" width="96" height="3" class="deck-pavement" />
                <line x1="2" y1="10" x2="98" y2="10" class="deck-line" />
                <!-- 主梁 -->
                <rect x="6" y="13" width="88" height="9" class="deck-girder" />
                <!-- 桥墩 -->
                <rect x="27" y="22" width="3" height="26" class="deck-pier" />
                <rect x="70" y="22" width="3" height="26" class="deck-pier" />
                <!-- 桥台 -->
                <rect x="3" y="22" width="4" height="26" class="deck-abutment" />
                <rect x="93" y="22" width="4" height="26" class="deck-abutment" />
                <!-- 基础 -->
                <rect x="1" y="48" width="8" height="4" class="deck-foundation" />
                <rect x="25" y="48" width="7" height="4" class="deck-foundation" />
                <rect x="68" y="48" width="7" height="4" class="deck-foundation" />
                <rect x="91" y="48" width="8" height="4" class="deck-foundation" />
                <!-- 构件文字 -->
                <text x="50" y="7" class="deck-text" text-anchor="middle">桥面铺装层</text>
                <text x="50" y="19" class="deck-text" text-anchor="middle">上部承重构件（主梁）</text>
                <text x="28.5" y="38" class="deck-text-sm" text-anchor="middle">1#墩</text>
                <text x="71.5" y="38" class="deck-text-sm" text-anchor="middle">2#墩</text>
                <text x="5" y="40" class="deck-text-sm" text-anchor="middle">0#台</text>
                <text x="95" y="40" class="deck-text-sm" text-anchor="middle">3#台</text>
                <text x="50" y="56" class="deck-text" text-anchor="middle">基础 / 地基</text>
                <!-- 病害证据点 -->
                <g v-for="(ev, idx) in current.病害证据" :key="idx">
                  <circle
                    :cx="ev.x"
                    :cy="Math.round((ev.y / 100) * 58)"
                    r="2.4"
                    class="evidence-dot"
                    :class="sevClass(ev.严重度)"
                  />
                  <text
                    :x="ev.x + 1.2"
                    :y="Math.round((ev.y / 100) * 58) - 1.2"
                    class="evidence-label"
                  >{{ ev.证据号 }}</text>
                </g>
              </svg>
              <span v-if="!isDraft" class="deck-lock-mask">剖切图已提交，证据锁定；如需修改请撤回后由新会议处理</span>
            </div>

            <!-- 证据清单 -->
            <table class="evidence-table">
              <thead>
                <tr><th>编号</th><th>构件</th><th>病害</th><th>严重度</th><th>描述 / 照片</th></tr>
              </thead>
              <tbody>
                <tr v-for="ev in current.病害证据" :key="ev.证据号">
                  <td>{{ ev.证据号 }}</td>
                  <td>{{ ev.构件 }}</td>
                  <td>{{ ev.病害类型 }}</td>
                  <td><span :class="`sev-${sevClass(ev.严重度)}`">{{ ev.严重度 }}</span></td>
                  <td>{{ ev.描述 }} <em v-if="ev.照片" class="muted-text">（{{ ev.照片 }}）</em></td>
                </tr>
                <tr v-if="!current.病害证据?.length">
                  <td colspan="5" class="empty-state">尚未放置病害证据，在剖切图上点击构件开始标注</td>
                </tr>
              </tbody>
            </table>
          </section>

          <!-- 操作面板：按当前状态切换 -->
          <section class="panel action-panel">
            <div class="panel-head"><h4>会签操作</h4></div>

            <!-- 草稿：检查员录入 -->
            <div v-if="isDraft" class="action-body">
              <p class="role-tag role-inspector">检查员阶段 · 录入检测信息与病害证据</p>
              <label class="field">
                <span>桥位编号（关联既有桥位档案）</span>
                <input v-model="draftForm.桥位编号" placeholder="如 BRID-1001，可留空" />
              </label>
              <label class="field">
                <span>日常巡检评分</span>
                <input v-model="draftForm.日常评分" placeholder="如 88，用于与专项评分比对" />
              </label>
              <template v-if="placing">
                <label class="field">
                  <span>病害类型</span>
                  <input v-model="evidenceForm.病害类型" placeholder="如 横向裂缝" />
                </label>
                <label class="field">
                  <span>严重度</span>
                  <select v-model="evidenceForm.严重度">
                    <option>轻</option><option>中</option><option>重</option><option>危急</option>
                  </select>
                </label>
                <label class="field">
                  <span>构件（点击剖切图自动带入）</span>
                  <input v-model="evidenceForm.构件" />
                </label>
                <label class="field">
                  <span>描述</span>
                  <textarea v-model="evidenceForm.描述" rows="2"></textarea>
                </label>
                <label class="field">
                  <span>证据照片名</span>
                  <input v-model="evidenceForm.照片" placeholder="IMG_0001.jpg" />
                </label>
                <div class="field-row">
                  <button class="btn primary" type="button" @click="saveEvidence">确认放置于 ({{ pendingPoint?.x }}, {{ pendingPoint?.y }})</button>
                  <button class="btn ghost" type="button" @click="cancelPlace">取消</button>
                </div>
              </template>
              <p v-else class="muted-text">在左侧剖切图上点击任意构件，即可放置病害证据。</p>
              <button class="btn primary wide" type="button" @click="submitMeeting">
                提交主管会签（需 ≥1 处证据）
              </button>
            </div>

            <!-- 待主管会签：主管选限载结论 -->
            <div v-else-if="isWaiting" class="action-body">
              <p class="role-tag role-supervisor">主管阶段 · 选择限载结论（多人同时签认只放行一份）</p>
              <label class="field">
                <span>主管身份</span>
                <select v-model="signForm.主管">
                  <option>主管·周建国</option>
                  <option>主管·郑副主管</option>
                  <option>主管·吴总工</option>
                </select>
              </label>
              <label class="field">
                <span>专项检测评分（0-100）</span>
                <input v-model="signForm.专项评分" placeholder="如 66" />
                <small class="muted-text">日常评分为 {{ current.日常评分 || '未录入' }}；两者折算等级不一致将标记冲突，以本次专项会签为准</small>
              </label>
              <label class="field">
                <span>限载结论</span>
                <div class="conclusion-grid">
                  <button
                    v-for="c in conclusions"
                    :key="c"
                    type="button"
                    class="conclusion-btn"
                    :class="[`concl-${conclusionClass(c)}`, { picked: signForm.限载结论 === c }]"
                    @click="signForm.限载结论 = c; previewGrade(c)"
                  >{{ c }}</button>
                </div>
              </label>
              <label class="field">
                <span>会签意见</span>
                <textarea v-model="signForm.意见" rows="2" placeholder="限载依据、处置要求"></textarea>
              </label>
              <p v-if="gradePreview" class="grade-preview">
                该结论对应技术等级 <b>{{ gradePreview.grade }}</b>，风险 <b :class="`risk-text-${riskClass(gradePreview.risk)}`">{{ gradePreview.risk }}</b>
              </p>
              <div class="field-row">
                <button class="btn primary" type="button" @click="sign(false)">提交签认</button>
                <button class="btn" type="button" @click="sign(true)">模拟断网重传签认</button>
              </div>
              <p class="muted-text small">重传会携带同一请求号，服务端幂等：已生效直接返回原决定，不会重复落结论。</p>
            </div>

            <!-- 已会签：生成工程任务 -->
            <div v-else-if="isSigned" class="action-body">
              <p class="role-tag role-engineer">工程阶段 · 会签通过，最后生成工程任务</p>
              <div class="decision-card">
                <div>限载结论：<b :class="`risk-text-${riskClass(current.风险等级)}`">{{ current.限载结论 }}</b></div>
                <div>技术等级：<b>{{ current.技术等级 }}</b> ｜ 风险等级：<b>{{ current.风险等级 }}</b></div>
                <div>专项评分：{{ current.专项评分 }} ｜ 日常评分：{{ current.日常评分 }}
                  <i v-if="current.评分冲突" class="conflict-flag">冲突，以专项会签为准</i>
                </div>
              </div>
              <label class="field">
                <span>工程名称（可改）</span>
                <input v-model="taskForm.工程名称" placeholder="留空按桥梁与结论自动生成" />
              </label>
              <label class="field">
                <span>工程类型（可改）</span>
                <input v-model="taskForm.工程类型" placeholder="留空按结论自动匹配" />
              </label>
              <button class="btn primary wide" type="button" @click="generateTask">生成工程任务并同步工程清单</button>
            </div>

            <!-- 已生成任务 -->
            <div v-else-if="isTasked" class="action-body">
              <p class="role-tag role-done">会签闭环完成</p>
              <div class="decision-card">
                <div>最终结论：<b :class="`risk-text-${riskClass(current.风险等级)}`">{{ current.限载结论 }}</b> · {{ current.技术等级 }}</div>
                <div>工程任务：<b>{{ current.同步?.工程清单 }}</b>（待开工）</div>
              </div>
            </div>

            <!-- 已撤回 -->
            <div v-else class="action-body">
              <p class="role-tag role-withdrawn">该会议已撤回（终态，状态图不回退）</p>
              <button v-if="current.接续会议" class="btn primary" type="button" @click="select(current.接续会议)">
                打开接续会议 {{ successorCode(current.接续会议) }}
              </button>
              <p v-else class="muted-text">接续会议生成中…</p>
            </div>
          </section>
        </div>

        <!-- 同步状态 + 队列 + 日志 -->
        <div class="wb-bottom">
          <section class="panel sync-panel">
            <div class="panel-head"><h4>会签结果同步</h4></div>
            <ul class="sync-list">
              <li :class="{ done: current.同步?.档案 }">
                <span class="sync-dot"></span>定检档案
                <em>{{ current.同步?.档案 ? `已更新 ${current.同步.档案.桥位编号}（历史等级留存）` : '会签后写入' }}</em>
              </li>
              <li :class="{ done: current.同步?.巡检通知 }">
                <span class="sync-dot"></span>巡检通知
                <em>{{ current.同步?.巡检通知 || '会签后下发' }}</em>
              </li>
              <li :class="{ done: current.同步?.工程清单 }">
                <span class="sync-dot"></span>工程清单
                <em>{{ current.同步?.工程清单 || '生成任务时写入' }}</em>
              </li>
            </ul>
          </section>

          <section class="panel queue-panel">
            <div class="panel-head">
              <h4>签认队列</h4>
              <span class="muted-text">会签号 {{ current.会议全称 }}</span>
            </div>
            <ul v-if="current.队列?.length" class="queue-list">
              <li v-for="q in current.队列" :key="q.请求号">
                <span class="queue-state" :class="q.状态">{{ q.状态 === 'adopted' ? '放行' : '未放行' }}</span>
                {{ q.主管 }} → {{ q.结论 }}
                <em class="muted-text">{{ q.请求号 }} · {{ q.说明 }}</em>
              </li>
            </ul>
            <p v-else class="muted-text">暂无并发签认请求</p>
          </section>

          <section class="panel log-panel">
            <div class="panel-head"><h4>会议日志（单向流转留痕）</h4></div>
            <ol class="log-list">
              <li v-for="(l, i) in current.日志" :key="i">
                <span class="log-time">{{ l.时间 }}</span>
                <b>{{ l.角色 }}</b> {{ l.动作 }}
                <em class="muted-text">{{ l.说明 }}</em>
              </li>
            </ol>
          </section>
        </div>
      </main>

      <main v-else class="workbench empty-bench">
        <p class="empty-state">从左侧选择一场会签会议，或新建一场。</p>
      </main>
    </div>

    <!-- 新建会议弹层 -->
    <div v-if="creating" class="modal-mask" @click.self="creating = false">
      <form class="modal-card" @submit.prevent="createMeeting">
        <h3>新建会签会议</h3>
        <label class="field">
          <span>检测编号 *</span>
          <input v-model="createForm.检测编号" placeholder="如 JC-2026-1001" />
        </label>
        <label class="field">
          <span>桥梁名称 *</span>
          <input v-model="createForm.桥梁名称" placeholder="如 某某路跨线桥" />
        </label>
        <label class="field">
          <span>桥位编号（选填，关联既有桥位）</span>
          <input v-model="createForm.桥位编号" placeholder="如 BRID-1002" />
        </label>
        <label class="field">
          <span>检查员</span>
          <input v-model="createForm.检查员" placeholder="检查员·李工" />
        </label>
        <p v-if="actionMessage" class="error-text">{{ actionMessage }}</p>
        <div class="field-row end">
          <button class="btn ghost" type="button" @click="creating = false">取消</button>
          <button class="btn primary" type="submit">创建并打开剖切图</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span v-if="actionMessage" :class="actionOk ? 'ok-text' : 'error-text'">{{ actionMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Meeting = Record<string, any>
type Board = Record<string, any>

const ENDPOINT = '/api/countersign'
const statuses = ['草稿', '待主管会签', '已会签', '已生成任务', '已撤回']
const stateSteps = ['草稿', '待主管会签', '已会签', '已生成任务']
const conclusions = ['正常通行', '限速通行', '限载通行', '禁止通行']

const meetings = ref<Meeting[]>([])
const current = ref<Meeting | null>(null)
const board = ref<Board>({})
const actionMessage = ref('')
const actionOk = ref(true)
const creating = ref(false)
const recoverCode = ref('')

const filters = reactive<Record<string, string>>({ keyword: '', status: '', bridge: '' })
const createForm = reactive({ 检测编号: '', 桥梁名称: '', 桥位编号: '', 检查员: '检查员·李工' })
const draftForm = reactive({ 桥位编号: '', 日常评分: '' })
const taskForm = reactive({ 工程名称: '', 工程类型: '' })

const placing = ref(false)
const pendingPoint = ref<{ x: number; y: number } | null>(null)
const evidenceForm = reactive({ 病害类型: '', 严重度: '中', 构件: '', 描述: '', 照片: '' })

const signForm = reactive({
  主管: '主管·周建国',
  专项评分: '',
  限载结论: '',
  意见: '',
  requestId: '',
})
const gradePreview = ref<{ grade: string; risk: string } | null>(null)

// 剖切图构件命中分区（svg 坐标系 y 为 0-58，换算回百分比 0-100）
function hitComponent(x: number, sy: number): string {
  const y = (sy / 58) * 100
  if (y <= 22) return x < 40 ? '0#跨桥面铺装' : x > 65 ? '3#跨桥面铺装' : '桥面铺装'
  if (y <= 38) return '上部主梁'
  if (x < 12) return '0#台台帽/台身'
  if (x < 34) return '1#墩墩柱'
  if (x < 74) return '2#墩墩柱'
  if (x > 88) return '3#台台帽/台身'
  return '桥下空间/基础'
}

// ---------------------------------------------------------------- 状态派生

const isDraft = computed(() => current.value?.status === '草稿')
const isWaiting = computed(() => current.value?.status === '待主管会签')
const isSigned = computed(() => current.value?.status === '已会签')
const isTasked = computed(() => current.value?.status === '已生成任务')
const canWithdraw = computed(() =>
  ['草稿', '待主管会签', '已会签', '已生成任务'].includes(current.value?.status),
)

const riskPills = computed(() => {
  const dist = board.value.风险等级分布 || {}
  return [
    { label: '极高', value: dist['极高'] || 0 },
    { label: '高', value: dist['高'] || 0 },
    { label: '中', value: dist['中'] || 0 },
    { label: '低', value: dist['低'] || 0 },
  ]
})

function statusClass(s: string): string {
  return { 草稿: 'draft', 待主管会签: 'waiting', 已会签: 'signed', 已生成任务: 'tasked', 已撤回: 'withdrawn' }[s] || 'draft'
}
function stepClass(i: number): string {
  if (!current.value) return ''
  const cur = stateSteps.indexOf(current.value.status)
  if (current.value.status === '已撤回') return 'reached'
  return i < cur ? 'reached' : i === cur ? 'current' : ''
}
function riskClass(risk?: string): string {
  return { 极高: 'critical', 高: 'high', 中: 'mid', 低: 'low' }[risk || ''] || 'low'
}
function conclusionClass(c: string): string {
  return { 正常通行: 'normal', 限速通行: 'slow', 限载通行: 'limit', 禁止通行: 'ban' }[c] || 'normal'
}
function sevClass(sev?: string): string {
  return { 轻: 'light', 中: 'mid', 重: 'heavy', 危急: 'critical' }[sev || '中'] || 'mid'
}
function successorCode(id: number): string {
  return meetings.value.find(m => m.id === id)?.会议全称 || ''
}
function previewGrade(c: string) {
  const map: Record<string, { grade: string; risk: string }> = {
    正常通行: { grade: '1类', risk: '低' },
    限速通行: { grade: '3类', risk: '中' },
    限载通行: { grade: '4类', risk: '高' },
    禁止通行: { grade: '5类', risk: '极高' },
  }
  gradePreview.value = map[c] || null
}

function flash(message: string, ok = true) {
  actionMessage.value = message
  actionOk.value = ok
}

// ---------------------------------------------------------------- 数据加载

async function reload(keepCurrent = true) {
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString()
  const resp = await request(`${ENDPOINT}?${query}`)
  if (!resp.ok) {
    flash('会签列表读取失败', false)
    return
  }
  const payload = await resp.json()
  meetings.value = payload.items || []
  if (keepCurrent && current.value) {
    const latest = meetings.value.find((m: Meeting) => m.id === current.value!.id)
    if (latest) await select(latest.id, true)
  }
  await loadBoard()
}

async function loadBoard() {
  const resp = await request(`${ENDPOINT}/risk-board`)
  if (resp.ok) board.value = await resp.json()
}

async function select(id: number, silent = false) {
  const resp = await request(`${ENDPOINT}/${id}`)
  if (!resp.ok) {
    if (!silent) flash('会议读取失败', false)
    return
  }
  current.value = await resp.json()
  const meeting = current.value as Meeting
  draftForm.桥位编号 = meeting.桥位编号 || ''
  draftForm.日常评分 = meeting.日常评分 || ''
  taskForm.工程名称 = ''
  taskForm.工程类型 = ''
  signForm.主管 = meeting.主管 || '主管·周建国'
  signForm.专项评分 = meeting.专项评分 || ''
  signForm.限载结论 = meeting.限载结论 || ''
  signForm.意见 = ''
  signForm.requestId = ''
  gradePreview.value = null
  placing.value = false
  pendingPoint.value = null
  if (signForm.限载结论) previewGrade(signForm.限载结论)
}

// ---------------------------------------------------------------- 检查员

function openCreate() {
  Object.assign(createForm, { 检测编号: '', 桥梁名称: '', 桥位编号: '', 检查员: '检查员·李工' })
  creating.value = true
}

async function createMeeting() {
  const resp = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: { ...createForm } }) })
  const payload = await resp.json()
  if (!payload.ok) {
    flash(payload.message, false)
    return
  }
  creating.value = false
  flash(payload.message)
  await reload(false)
  await select(payload.entry.id)
}

function onDeckClick(event: MouseEvent) {
  if (!isDraft.value || !current.value) return
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  // SVG viewBox 宽 100 高 58，统一换算成百分比坐标（0-100）
  const sx = ((event.clientX - rect.left) / rect.width) * 100
  const sy = ((event.clientY - rect.top) / rect.height) * 58
  const x = Math.round(sx)
  const y = Math.round((sy / 58) * 100)
  pendingPoint.value = { x, y }
  Object.assign(evidenceForm, { 病害类型: '', 严重度: '中', 构件: hitComponent(x, sy), 描述: '', 照片: '' })
  placing.value = true
}

function cancelPlace() {
  placing.value = false
  pendingPoint.value = null
}

async function saveEvidence() {
  if (!current.value || !pendingPoint.value) return
  if (!evidenceForm.病害类型.trim()) {
    flash('请填写病害类型', false)
    return
  }
  const resp = await request(`${ENDPOINT}/${current.value.id}/evidence`, {
    method: 'POST',
    body: JSON.stringify({ values: { ...pendingPoint.value, ...evidenceForm } }),
  })
  const payload = await resp.json()
  if (!payload.ok) {
    flash(payload.message, false)
    return
  }
  current.value = payload.entry
  placing.value = false
  pendingPoint.value = null
  flash('病害证据已放置到剖切图')
}

async function submitMeeting() {
  if (!current.value) return
  if (draftForm.桥位编号 !== (current.value.桥位编号 || '') || draftForm.日常评分 !== (current.value.日常评分 || '')) {
    await request(`${ENDPOINT}/${current.value.id}/inspection`, {
      method: 'POST',
      body: JSON.stringify({ values: { 桥位编号: draftForm.桥位编号, 日常评分: draftForm.日常评分 } }),
    })
  }
  const resp = await request(`${ENDPOINT}/${current.value.id}/submit`, {
    method: 'POST',
    body: JSON.stringify({ values: { 日常评分: draftForm.日常评分 } }),
  })
  const payload = await resp.json()
  if (!payload.ok) {
    flash(payload.message, false)
    return
  }
  current.value = payload.entry
  flash(payload.message)
  await loadBoard()
}

// ---------------------------------------------------------------- 主管

function newRequestId(): string {
  return `REQ-WEB-${Date.now()}-${Math.floor(Math.random() * 1000)}`
}

async function sign(simulateOffline: boolean) {
  if (!current.value) return
  if (!signForm.限载结论) {
    flash('请先选择限载结论', false)
    return
  }
  if (!signForm.专项评分.trim()) {
    flash('请填写专项检测评分', false)
    return
  }
  if (!signForm.requestId) signForm.requestId = newRequestId()
  const body = {
    values: {
      主管: signForm.主管,
      专项评分: signForm.专项评分,
      限载结论: signForm.限载结论,
      意见: signForm.意见,
      request_id: signForm.requestId,
    },
  }
  const query = `?request_id=${encodeURIComponent(signForm.requestId)}`
  const doCall = () => request(`${ENDPOINT}/${current.value!.id}/sign${query}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

  let resp: Response
  if (simulateOffline) {
    // 模拟“连接中断后重传”：同一 request_id 连打两次，服务端只放行一份决定
    await doCall().catch(() => null)
    flash('首包已送达（模拟连接中断），正在凭同一请求号重传…')
    resp = await doCall()
  } else {
    resp = await doCall()
  }
  const payload = await resp.json()
  flash(payload.message, payload.ok)
  await reload()
}

async function recoverQueue() {
  const code = recoverCode.value.trim()
  if (!code) {
    flash('请输入会签号', false)
    return
  }
  // 先查看队列，再把断线前的决定（若有）以同请求号幂等重放
  const qresp = await request(`${ENDPOINT}/queue?code=${encodeURIComponent(code)}`)
  if (!qresp.ok) {
    flash('会签号不存在，无法恢复', false)
    return
  }
  const snap = await qresp.json()
  if (snap.status === '待主管会签') {
    flash(`${code} 队列已恢复：当前待主管会签，请到主管面板完成签认`)
    const target = meetings.value.find(m => m.会议全称 === code)
    if (target) await select(target.id)
    return
  }
  flash(`${code} 队列已恢复：${snap.队列.length} 条请求，结论「${snap.限载结论 || '无'}」`)
  const target = meetings.value.find(m => m.会议全称 === code)
  if (target) await select(target.id)
}

// ---------------------------------------------------------------- 工程任务

async function generateTask() {
  if (!current.value) return
  const resp = await request(`${ENDPOINT}/${current.value.id}/task`, {
    method: 'POST',
    body: JSON.stringify({ values: { 工程名称: taskForm.工程名称, 工程类型: taskForm.工程类型 } }),
  })
  const payload = await resp.json()
  if (!payload.ok) {
    flash(payload.message, false)
    return
  }
  current.value = payload.entry
  flash(payload.message)
  await loadBoard()
}

// ---------------------------------------------------------------- 撤回

async function withdraw() {
  if (!current.value) return
  const reason = window.prompt('撤回不允许回退，将以同一会签号生成新届次会议。请填写撤回原因：', '检测资料需补充复核')
  if (reason === null) return
  const resp = await request(`${ENDPOINT}/${current.value.id}/withdraw`, {
    method: 'POST',
    body: JSON.stringify({ values: { 原因: reason, 操作人: signForm.主管 || current.value.主管 } }),
  })
  const payload = await resp.json()
  if (!payload.ok) {
    flash(payload.message, false)
    return
  }
  flash(payload.message)
  await reload(false)
  await select(payload.entry.id)
}

onMounted(() => {
  void reload(false)
})
</script>

<style scoped>
.csd-page { display: block; }
.muted-text { color: var(--muted); font-style: normal; }
.small { font-size: 12px; }
.ok-text { color: #067647; }
.danger { color: #b42318; border-color: #f0a39a; }

/* 风险看板 */
.risk-board { display: grid; grid-template-columns: 1.1fr 1.3fr 1fr; gap: 12px; margin-bottom: 14px; }
.risk-col { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.risk-col h3 { margin: 0 0 8px; font-size: 14px; }
.risk-pills { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.risk-pill { font-size: 12px; border-radius: 999px; padding: 2px 10px; border: 1px solid var(--border); }
.risk-pill b { margin-left: 4px; }
.risk-pill.risk-critical { background: #fef3f2; border-color: #f04438; color: #b42318; }
.risk-pill.risk-high { background: #fffaeb; border-color: #f79009; color: #b54708; }
.risk-pill.risk-mid { background: #fffcf5; border-color: #eaaa00; color: #8a6100; }
.risk-pill.risk-low { background: #ecfdf3; border-color: #12b76a; color: #067647; }
.risk-conclusions { display: flex; gap: 6px; flex-wrap: wrap; }
.conclusion-chip { font-size: 12px; background: #f2f4f7; border-radius: 4px; padding: 2px 8px; }
.risk-bridge-list { list-style: none; margin: 0; padding: 0; font-size: 13px; display: flex; flex-direction: column; gap: 6px; }
.risk-bridge-list li { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.risk-bridge-list em { color: var(--muted); font-style: normal; font-size: 12px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.risk-critical { background: #f04438; }
.dot.risk-high { background: #f79009; }
.dot.risk-mid { background: #eaaa00; }
.dot.risk-low { background: #12b76a; }
.risk-text-critical { color: #b42318; font-weight: 600; }
.risk-text-high { color: #b54708; font-weight: 600; }
.risk-text-mid { color: #8a6100; font-weight: 600; }
.risk-text-low { color: #067647; font-weight: 600; }
.conflict-flag { font-style: normal; font-size: 11px; background: #fef3f2; color: #b42318; border: 1px solid #f0a39a; border-radius: 4px; padding: 0 5px; }
.task-flag { font-style: normal; font-size: 11px; background: #eff8ff; color: #175cd3; border: 1px solid #b2ccff; border-radius: 4px; padding: 0 5px; }
.queue-line { font-size: 13px; margin: 4px 0; }
.recover-box { display: flex; gap: 6px; margin-top: 8px; }
.recover-box input { flex: 1; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 12px; }

/* 布局 */
.csd-layout { display: grid; grid-template-columns: 300px 1fr; gap: 12px; align-items: start; }
.meeting-list { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 8px; position: sticky; top: 12px; }
.meeting-filter { display: flex; flex-direction: column; gap: 6px; margin-bottom: 8px; }
.meeting-filter input, .meeting-filter select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.meeting-list ul { list-style: none; margin: 0; padding: 0; max-height: 70vh; overflow-y: auto; }
.meeting-item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin-bottom: 6px; cursor: pointer; }
.meeting-item:hover { border-color: var(--brand); }
.meeting-item.active { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand); background: #f5f9ff; }
.meeting-item.withdrawn { opacity: 0.6; }
.meeting-item-head { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.meeting-item-head strong { font-size: 13px; }
.meeting-item-body { font-size: 12px; margin: 4px 0; }
.meeting-item-foot { font-size: 12px; display: flex; gap: 6px; align-items: center; }
.status-tag { font-size: 11px; padding: 1px 7px; border-radius: 999px; white-space: nowrap; }
.st-draft { background: #f2f4f7; color: #475467; }
.st-waiting { background: #eff8ff; color: #175cd3; }
.st-signed { background: #fffaeb; color: #b54708; }
.st-tasked { background: #ecfdf3; color: #067647; }
.st-withdrawn { background: #fef3f2; color: #b42318; }

/* 工作台 */
.workbench { display: flex; flex-direction: column; gap: 12px; }
.empty-bench { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 40px; text-align: center; }
.wb-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.wb-head h3 { margin: 0; font-size: 16px; }
.wb-head h3 small { font-weight: normal; color: #b54708; font-size: 12px; margin-left: 8px; }
.state-track { list-style: none; display: flex; margin: 0; padding: 0; background: #fff; border: 1px solid var(--border); border-radius: 8px; }
.state-step { flex: 1; display: flex; align-items: center; gap: 8px; padding: 10px 12px; position: relative; font-size: 13px; color: var(--muted); }
.state-step:not(:last-child)::after { content: ''; position: absolute; right: -6px; top: 50%; width: 12px; height: 12px; border-top: 2px solid var(--border); border-right: 2px solid var(--border); transform: translateY(-50%) rotate(45deg); z-index: 1; background: #fff; }
.state-dot { width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; background: #e4e7ec; color: #667085; font-size: 12px; }
.state-step.reached .state-dot { background: #12b76a; color: #fff; }
.state-step.reached { color: #067647; }
.state-step.current .state-dot { background: var(--brand); color: #fff; }
.state-step.current { color: #175cd3; font-weight: 600; }

.wb-grid { display: grid; grid-template-columns: 1.5fr 1fr; gap: 12px; align-items: start; }
.wb-bottom { display: grid; grid-template-columns: 1fr 1fr 1.2fr; gap: 12px; align-items: start; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.panel-head h4 { margin: 0; font-size: 14px; }

/* 剖切图 */
.deck-canvas { position: relative; border: 1px solid var(--border); border-radius: 6px; background: #fbfcfe; cursor: crosshair; }
.deck-canvas.locked { cursor: not-allowed; }
.deck-canvas svg { width: 100%; height: 220px; display: block; }
.deck-pavement { fill: #344054; }
.deck-girder { fill: #d1e0ff; stroke: #84a9ff; stroke-width: 0.4; }
.deck-pier { fill: #cbd5e1; stroke: #94a3b8; stroke-width: 0.3; }
.deck-abutment { fill: #b9c6d8; stroke: #7c8db5; stroke-width: 0.3; }
.deck-foundation { fill: #98a2b3; }
.deck-line { stroke: #101828; stroke-width: 0.4; }
.deck-text { font-size: 3.2px; fill: #475467; }
.deck-text-sm { font-size: 2.8px; fill: #667085; }
.evidence-dot { stroke: #fff; stroke-width: 0.5; }
.evidence-dot.sev-light { fill: #f79009; }
.evidence-dot.sev-mid { fill: #ef6820; }
.evidence-dot.sev-heavy { fill: #e31b54; }
.evidence-dot.sev-critical { fill: #d92d20; }
.evidence-label { font-size: 2.6px; fill: #b42318; font-weight: 700; }
.deck-lock-mask { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(242, 244, 247, 0.55); color: #475467; font-size: 13px; border-radius: 6px; text-align: center; padding: 0 20px; }

.evidence-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
.evidence-table th, .evidence-table td { border: 1px solid var(--border); padding: 5px 8px; font-size: 12px; text-align: left; }
.sev-light { color: #b54708; }
.sev-mid { color: #c2410c; }
.sev-heavy { color: #c11574; font-weight: 600; }
.sev-critical { color: #b42318; font-weight: 700; }

/* 操作面板 */
.role-tag { font-size: 12px; border-radius: 4px; padding: 4px 8px; margin: 0 0 10px; }
.role-inspector { background: #eff8ff; color: #175cd3; }
.role-supervisor { background: #fffaeb; color: #b54708; }
.role-engineer { background: #ecfdf3; color: #067647; }
.role-done { background: #ecfdf3; color: #067647; }
.role-withdrawn { background: #fef3f2; color: #b42318; }
.field { display: block; margin-bottom: 10px; }
.field > span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 3px; }
.field input, .field select, .field textarea { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; font-family: inherit; }
.field small { display: block; margin-top: 3px; }
.field-row { display: flex; gap: 8px; align-items: center; }
.field-row.end { justify-content: flex-end; }
.btn.wide { width: 100%; margin-top: 6px; }
.conclusion-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.conclusion-btn { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 8px 6px; cursor: pointer; font-size: 13px; }
.conclusion-btn.picked { outline: 2px solid var(--brand); outline-offset: -1px; font-weight: 600; }
.conclusion-btn.concl-normal { border-color: #6ce9a6; }
.conclusion-btn.concl-slow { border-color: #fdb022; }
.conclusion-btn.concl-limit { border-color: #f97066; }
.conclusion-btn.concl-ban { border-color: #d92d20; background: #fef3f2; }
.grade-preview { font-size: 13px; background: #f2f4f7; border-radius: 6px; padding: 6px 10px; }
.decision-card { background: #f9fafb; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 13px; display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }

/* 同步/队列/日志 */
.sync-list, .queue-list, .log-list { list-style: none; margin: 0; padding: 0; font-size: 12px; display: flex; flex-direction: column; gap: 7px; }
.sync-list li { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sync-list em { color: var(--muted); font-style: normal; width: 100%; padding-left: 18px; }
.sync-dot { width: 9px; height: 9px; border-radius: 50%; background: #d0d5dd; }
.sync-list.done .sync-dot, .sync-list li.done .sync-dot { background: #12b76a; }
.queue-list li { border-left: 3px solid var(--border); padding-left: 8px; line-height: 1.5; }
.queue-state { font-style: normal; font-size: 11px; border-radius: 4px; padding: 0 5px; margin-right: 4px; }
.queue-state.adopted { background: #ecfdf3; color: #067647; }
.queue-state.skipped { background: #f2f4f7; color: #667085; }
.queue-list em { display: block; font-style: normal; }
.log-list { max-height: 220px; overflow-y: auto; }
.log-list li { line-height: 1.5; border-bottom: 1px dashed #eaecf0; padding-bottom: 4px; }
.log-time { color: var(--muted); margin-right: 6px; font-size: 11px; }

/* 弹层 */
.modal-mask { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal-card { background: #fff; border-radius: 10px; padding: 18px 20px; width: 420px; max-width: 92vw; }
.modal-card h3 { margin: 0 0 12px; }
</style>
