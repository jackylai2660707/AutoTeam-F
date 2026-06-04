<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-xl font-bold text-ink-950">Team 成员</h2>
      <button @click="fetchMembers" :disabled="loading"
        class="px-3 py-1.5 bg-surface hover:bg-ink-100 text-sm rounded-lg border border-hairline transition disabled:opacity-50 focus-ring text-ink-700">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
    </div>

    <div v-if="error" class="mb-4 px-4 py-3 rounded-lg text-sm bg-rose-50 text-rose-700 border border-rose-200">
      {{ error }}
    </div>

    <div v-if="data" class="space-y-4">
      <!-- 统计 -->
      <div class="flex gap-2 text-sm flex-wrap">
        <span class="px-3 py-1.5 bg-ink-50 rounded-lg text-ink-600 border border-hairline">成员: <span class="text-ink-950 font-medium">{{ data.total }}</span></span>
        <span class="px-3 py-1.5 bg-emerald-50 rounded-lg text-emerald-800 border border-emerald-200">
          ChatGPT 子席位:
          <span class="font-medium">{{ seatSummary.child_chatgpt || 0 }}/{{ seatSummary.max_child_chatgpt || 2 }}</span>
        </span>
        <span class="px-3 py-1.5 bg-amber-50 rounded-lg text-amber-800 border border-amber-200">
          Codex/usage_based:
          <span class="font-medium">{{ seatSummary.codex || 0 }}</span>
        </span>
        <span v-if="data.invites > 0" class="px-3 py-1.5 bg-amber-50 rounded-lg text-amber-800 border border-amber-200">待接受邀请: <span class="font-medium">{{ data.invites }}</span></span>
      </div>

      <!-- 成员表格 -->
      <div class="glass rounded-lg overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-ink-500 text-left border-b border-hairline">
                <th class="px-4 py-3 font-medium">#</th>
                <th class="px-4 py-3 font-medium">邮箱</th>
                <th class="px-4 py-3 font-medium">角色</th>
                <th class="px-4 py-3 font-medium">席位</th>
                <th class="px-4 py-3 font-medium">类型</th>
                <th class="px-4 py-3 font-medium">来源</th>
                <th class="px-4 py-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(m, i) in data.members" :key="m.email + m.type"
                class="border-b border-hairline hover:bg-ink-50 transition">
                <td class="px-4 py-3 text-ink-500">{{ i + 1 }}</td>
                <td class="px-4 py-3 font-mono text-xs">{{ m.email }}</td>
                <td class="px-4 py-3">
                  <span class="px-2 py-0.5 rounded text-xs font-medium"
                    :class="{
                      'bg-indigo-50 text-indigo-700': m.role === 'account-owner',
                      'bg-sky-50 text-sky-700': m.role === 'account-admin',
                      'bg-ink-50 text-ink-600': m.role !== 'account-owner' && m.role !== 'account-admin',
                    }">
                    {{ m.role || 'member' }}
                  </span>
                </td>
                <td class="px-4 py-3">
                  <span class="px-2 py-0.5 rounded text-xs font-bold uppercase tracking-wide border"
                    :class="seatClass(m)">
                    {{ seatLabel(m) }}
                  </span>
                </td>
                <td class="px-4 py-3">
                  <span class="px-2 py-0.5 rounded text-xs font-medium"
                    :class="m.type === 'invite' ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'">
                    {{ m.type === 'invite' ? '待接受' : '已加入' }}
                  </span>
                </td>
                <td class="px-4 py-3">
                  <span class="text-xs" :class="m.is_local ? 'text-sky-700' : 'text-ink-500'">
                    {{ m.is_local ? '本地管理' : '外部' }}
                  </span>
                </td>
                <td class="px-4 py-3 text-right">
                  <button
                    v-if="m.role !== 'account-owner'"
                    @click="removeMember(m)"
                    :disabled="removingId === memberKey(m)"
                    class="px-3 py-1.5 rounded-lg text-xs font-medium border transition"
                    :class="removingId === memberKey(m)
                      ? 'bg-ink-100 text-ink-400 border-hairline cursor-not-allowed'
                      : 'bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100'"
                  >
                    {{ removingId === memberKey(m) ? '处理中...' : '移出' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Loading -->
    <div v-else-if="loading" class="glass-soft rounded-lg h-64 shimmer-bg"></div>

    <!-- Empty -->
    <div v-else class="text-center text-ink-500 py-12">
      点击「刷新」加载 Team 成员列表
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { api } from '../api.js'

const data = ref(null)
const loading = ref(false)
const error = ref('')
const removingId = ref('')

const CACHE_KEY = 'autoteam_team_members'
const seatSummary = computed(() => data.value?.seat_summary || {})

function loadCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (raw) {
      const cached = JSON.parse(raw)
      // 缓存 10 分钟有效
      if (cached.time && Date.now() - cached.time < 600000) {
        return cached.data
      }
    }
  } catch {}
  return null
}

function saveCache(d) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ data: d, time: Date.now() }))
  } catch {}
}

function memberKey(member) {
  return `${member.type}:${member.user_id}:${member.email}`
}

function seatLabel(member) {
  if (member.type === 'invite') return 'Invite'
  if (member.seat_type === 'chatgpt') return 'ChatGPT'
  if (member.seat_type === 'codex') return 'Codex'
  return member.seat_type_raw || 'Unknown'
}

function seatClass(member) {
  if (member.type === 'invite') return 'bg-amber-50 text-amber-700 border-amber-200'
  if (member.seat_type === 'chatgpt') return 'bg-emerald-50 text-emerald-700 border-emerald-200'
  if (member.seat_type === 'codex') return 'bg-slate-50 text-slate-700 border-slate-200'
  return 'bg-ink-50 text-ink-600 border-hairline'
}

async function fetchMembers() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.getTeamMembers()
    saveCache(data.value)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function removeMember(member) {
  const actionText = member.type === 'invite' ? '取消邀请' : '移出 Team'
  const ok = window.confirm(`确认${actionText} ${member.email}？`)
  if (!ok) return

  removingId.value = memberKey(member)
  error.value = ''
  try {
    await api.removeTeamMember({
      email: member.email,
      user_id: member.user_id,
      type: member.type,
    })
    try {
      localStorage.removeItem(CACHE_KEY)
    } catch {}
    await fetchMembers()
  } catch (e) {
    error.value = e.message
  } finally {
    removingId.value = ''
  }
}

onMounted(() => {
  const cached = loadCache()
  if (cached) {
    data.value = cached
  } else {
    fetchMembers()
  }
})
</script>
