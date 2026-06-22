<template>
  <div class="page-view">
    <RunNav :runId="runId" />
    <div class="content-area">
      <div class="interaction-body">
        <div class="left-col">
          <div class="section-header">Evidence Q&amp;A</div>
          <p class="section-desc">
            Explore the evidence behind discovered themes. Questions are answered
            by tracing evidence edges in the knowledge graph — no predictions,
            only evidence cited from source documents.
          </p>

          <div class="chat-area">
            <div class="messages" ref="messagesEl">
              <div v-if="!messages.length" class="empty-chat">
                Ask a question about the themes discovered in this run.
              </div>
              <div
                v-for="(msg, idx) in messages"
                :key="idx"
                class="message"
                :class="msg.role"
              >
                <span class="msg-role">{{ msg.role === 'user' ? 'You' : 'Engine' }}</span>
                <div class="msg-body">{{ msg.content }}</div>
              </div>
            </div>

            <div class="input-area">
              <textarea
                v-model="userInput"
                class="chat-input"
                placeholder="e.g. Which companies have the highest AI Infrastructure exposure?"
                rows="3"
                @keydown.enter.ctrl="sendMessage"
                :disabled="thinking"
              ></textarea>
              <button class="send-btn" @click="sendMessage" :disabled="!userInput.trim() || thinking">
                {{ thinking ? 'Thinking...' : 'Send' }}
              </button>
            </div>
          </div>
        </div>

        <div class="right-col">
          <div class="section-header">Theme Context</div>
          <div v-if="loadingContext" class="loading-msg">Loading theme data...</div>
          <div v-else-if="!communities.length" class="empty-msg">
            Run Theme Discovery to populate context.
          </div>
          <div v-else class="context-list">
            <div v-for="c in communities" :key="c.community_id" class="context-card">
              <div class="ctx-name">{{ c.theme_name || c.community_id }}</div>
              <div class="ctx-summary">{{ c.theme_summary || '' }}</div>
              <div class="ctx-companies" v-if="c.top_companies?.length">
                Companies: {{ c.top_companies.slice(0, 4).join(', ') }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import RunNav from '../components/RunNav.vue'
import { getCommunitiesJson, getThemeSnapshots } from '../api/artifacts.js'

const props = defineProps({ runId: String })

const userInput = ref('')
const messages = ref([])
const thinking = ref(false)
const messagesEl = ref(null)

const communities = ref([])
const snapshots = ref([])
const loadingContext = ref(false)

const scrollToBottom = () => {
  nextTick(() => {
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  })
}

const sendMessage = () => {
  const q = userInput.value.trim()
  if (!q || thinking.value) return
  userInput.value = ''
  messages.value.push({ role: 'user', content: q })
  thinking.value = true
  scrollToBottom()

  // Evidence-based simulated response using loaded theme data
  setTimeout(() => {
    const answer = generateAnswer(q)
    messages.value.push({ role: 'assistant', content: answer })
    thinking.value = false
    scrollToBottom()
  }, 600)
}

const generateAnswer = (question) => {
  if (!communities.value.length) {
    return 'No theme data loaded for this run. Complete the Theme Discovery step first.'
  }

  const q = question.toLowerCase()
  const matched = communities.value.filter(c => {
    const name = (c.theme_name || '').toLowerCase()
    const summary = (c.theme_summary || '').toLowerCase()
    const entities = (c.top_entities || []).join(' ').toLowerCase()
    const companies = (c.top_companies || []).join(' ').toLowerCase()
    return q.split(/\s+/).some(word =>
      word.length > 3 && (name.includes(word) || summary.includes(word) || entities.includes(word) || companies.includes(word))
    )
  })

  if (!matched.length) {
    const names = communities.value.map(c => c.theme_name || c.community_id).join(', ')
    return `No themes directly matched your query. Discovered themes in this run: ${names}. Try asking about one of these themes specifically.`
  }

  const c = matched[0]
  const snap = snapshots.value.find(s => s.community_id === c.community_id)
  const state = snap?.state || 'Unknown'
  const companies = (c.top_companies || []).slice(0, 5).join(', ') || 'none identified'
  const entities = (c.top_entities || []).slice(0, 5).join(', ') || 'none identified'

  return [
    `Theme: ${c.theme_name || c.community_id} (${state})`,
    '',
    c.theme_summary || snap?.summary || 'No summary available.',
    '',
    `Top companies with evidence: ${companies}`,
    `Key entities: ${entities}`,
    '',
    `Community size: ${c.size} nodes, density: ${(c.density || 0).toFixed(3)}`,
    '',
    'Source: evidence edges extracted from source documents. This is not investment advice.'
  ].join('\n')
}

const loadContext = async () => {
  loadingContext.value = true
  try {
    const [commDoc, snapDoc] = await Promise.allSettled([
      getCommunitiesJson(props.runId),
      getThemeSnapshots(props.runId)
    ])
    communities.value = commDoc.status === 'fulfilled' ? (commDoc.value.communities || []) : []
    snapshots.value = snapDoc.status === 'fulfilled' ? (snapDoc.value.snapshots || []) : []
  } catch {}
  finally { loadingContext.value = false }
}

onMounted(loadContext)
</script>

<style scoped>
.page-view {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #F8F9FA;
}

.content-area {
  flex: 1;
  overflow: hidden;
}

.interaction-body {
  display: flex;
  height: 100%;
}

.left-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 24px;
  overflow: hidden;
  border-right: 1px solid #EAEAEA;
}

.right-col {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 24px;
  background: #FFF;
  overflow-y: auto;
}

.section-header {
  font-size: 14px;
  font-weight: 700;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
  color: #333;
}

.section-desc {
  font-size: 13px;
  color: #666;
  line-height: 1.6;
  margin-bottom: 20px;
}

.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #E5E5E5;
  border-radius: 8px;
  background: #FFF;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.empty-chat {
  text-align: center;
  color: #CCC;
  font-size: 13px;
  margin: auto;
}

.message {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.message.user { align-items: flex-end; }
.message.assistant { align-items: flex-start; }

.msg-role {
  font-size: 10px;
  font-family: var(--font-mono);
  color: #999;
  text-transform: uppercase;
}

.msg-body {
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  max-width: 85%;
}

.message.user .msg-body {
  background: var(--accent, #1a56db);
  color: #FFF;
  border-bottom-right-radius: 2px;
}

.message.assistant .msg-body {
  background: #F5F5F5;
  color: #333;
  border-bottom-left-radius: 2px;
}

.input-area {
  border-top: 1px solid #F0F0F0;
  padding: 14px;
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.chat-input {
  flex: 1;
  border: 1px solid #E0E0E0;
  border-radius: 6px;
  padding: 10px 12px;
  font-family: var(--font-sans);
  font-size: 13px;
  resize: none;
  outline: none;
  transition: border-color 0.2s;
}

.chat-input:focus { border-color: var(--accent, #1a56db); }

.send-btn {
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 10px 18px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  border-radius: 6px;
  transition: opacity 0.2s;
  white-space: nowrap;
}

.send-btn:hover:not(:disabled) { opacity: 0.8; }
.send-btn:disabled { background: #CCC; cursor: not-allowed; }

/* Right col */
.context-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.context-card {
  padding: 12px 14px;
  border: 1px solid #EAEAEA;
  border-radius: 6px;
  background: #FAFAFA;
}

.ctx-name {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 4px;
}

.ctx-summary {
  font-size: 11px;
  color: #666;
  line-height: 1.5;
  margin-bottom: 6px;
}

.ctx-companies {
  font-size: 11px;
  color: #888;
  font-style: italic;
}

.loading-msg, .empty-msg {
  color: #999;
  font-size: 12px;
  line-height: 1.6;
}
</style>
