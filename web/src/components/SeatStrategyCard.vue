<template>
  <div class="seat-card rounded-xl border border-ink-950/10 p-4 lg:p-5 overflow-hidden relative">
    <div class="relative flex flex-col xl:flex-row xl:items-center gap-4">
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 flex-wrap">
          <div class="w-10 h-10 rounded-xl bg-ink-950 text-white flex items-center justify-center shadow-sm">
            <Repeat2 class="w-4 h-4" :stroke-width="2.2" />
          </div>
          <div>
            <div class="text-[10px] uppercase tracking-[0.32em] text-ink-500 font-bold">
              Seat Swap Guard
            </div>
            <h2 class="text-lg font-black text-ink-950 tracking-tight">
              ChatGPT 子席位 {{ childChatgpt }}/{{ maxChildChatgpt }}
            </h2>
          </div>
          <span class="px-2 py-1 rounded-lg border text-[10px] uppercase tracking-widest font-bold"
            :class="modeClass">
            {{ modeText }}
          </span>
          <span class="px-2 py-1 rounded-lg border text-[10px] uppercase tracking-widest font-bold"
            :class="capOk ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-rose-50 text-rose-700 border-rose-200'">
            {{ capOk ? 'cap ok' : 'over cap' }}
          </span>
        </div>

        <div class="mt-3 h-2 rounded-full bg-white/70 border border-white overflow-hidden">
          <div class="h-full rounded-full transition-all duration-500"
            :class="capOk ? 'bg-emerald-500' : 'bg-rose-500'"
            :style="{ width: `${usagePct}%` }"></div>
        </div>

        <p class="mt-3 text-xs leading-relaxed text-ink-600 max-w-4xl">
          {{ guidanceText }}
        </p>
      </div>

      <div class="grid grid-cols-2 sm:grid-cols-4 xl:w-[520px] gap-2.5">
        <div v-for="metric in metrics" :key="metric.label"
          class="rounded-xl border bg-white/78 backdrop-blur px-3 py-3 shadow-sm">
          <div class="flex items-center gap-1.5 text-[10px] uppercase tracking-widest font-bold"
            :class="metric.tone">
            <component :is="metric.icon" class="w-3.5 h-3.5" :stroke-width="2.1" />
            {{ metric.label }}
          </div>
          <div class="mt-1.5 text-2xl font-black tabular text-ink-950">
            {{ metric.value }}
          </div>
          <div class="mt-0.5 text-[10px] text-ink-500">
            {{ metric.hint }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Archive, CloudUpload, Repeat2, ShieldCheck, TimerReset } from 'lucide-vue-next'

const props = defineProps({
  status: { type: Object, default: null },
})

const rotation = computed(() => props.status?.seat_rotation || {})
const maxChildChatgpt = computed(() => Number(rotation.value.max_child_chatgpt_seats ?? 2))
const childChatgpt = computed(() => Number(rotation.value.local_child_chatgpt_active ?? 0))
const cpaPublishable = computed(() => Number(rotation.value.local_cpa_team_publishable ?? 0))
const codexStandby = computed(() => Number(rotation.value.local_codex_standby ?? 0))
const codexRecovered = computed(() => Number(rotation.value.local_codex_recovered ?? 0))
const codexBlocked = computed(() => Number(rotation.value.local_codex_quota_blocked ?? 0))
const enabled = computed(() => rotation.value.enabled !== false)
const fallbackKick = computed(() => rotation.value.fallback_kick_enabled === true)
const capOk = computed(() => rotation.value.chatgpt_cap_ok !== false && childChatgpt.value <= maxChildChatgpt.value)
const usagePct = computed(() => {
  const max = Math.max(1, maxChildChatgpt.value)
  return Math.min(100, Math.max(0, (childChatgpt.value / max) * 100))
})

const modeText = computed(() => {
  if (!enabled.value) return 'seat swap off'
  return fallbackKick.value ? 'fallback kick on' : 'no kick'
})

const modeClass = computed(() => {
  if (!enabled.value) return 'bg-slate-50 text-slate-700 border-slate-200'
  if (fallbackKick.value) return 'bg-amber-50 text-amber-700 border-amber-200'
  return 'bg-emerald-50 text-emerald-700 border-emerald-200'
})

const guidanceText = computed(() => {
  if (!enabled.value) return 'Seat-swap 未开启：额度耗尽仍可能走旧的移出/邀请路径。'
  if (!capOk.value) return '本地已超过 ChatGPT 子席位上限，轮替会停止继续提升或创建，建议立即检查 Team 成员。'
  if (codexStandby.value === 0) return '目前没有 codex standby。第一次子号耗尽时会先降级旧号，再创建或复用其他号补上 ChatGPT 席位。'
  if (codexRecovered.value > 0) return '已有可复用 codex standby；下次 ChatGPT 子号耗尽时会优先提升旧号并重新 OAuth。'
  return 'codex standby 都在等待 5h/周限恢复；如果此时需要补位，会注册新账号，但不会超过 ChatGPT 子席位上限。'
})

const metrics = computed(() => [
  {
    label: 'CPA OAuth',
    value: cpaPublishable.value,
    hint: '只发布 ChatGPT seat',
    icon: CloudUpload,
    tone: 'text-sky-700',
  },
  {
    label: 'Codex Standby',
    value: codexStandby.value,
    hint: `${codexRecovered.value} 可复用`,
    icon: Archive,
    tone: 'text-amber-700',
  },
  {
    label: '等待刷新',
    value: codexBlocked.value,
    hint: '5h / weekly block',
    icon: TimerReset,
    tone: 'text-orange-700',
  },
  {
    label: '硬上限',
    value: maxChildChatgpt.value,
    hint: '远端满员即刹车',
    icon: ShieldCheck,
    tone: capOk.value ? 'text-emerald-700' : 'text-rose-700',
  },
])
</script>
