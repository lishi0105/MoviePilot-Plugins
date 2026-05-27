<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { unwrapResponse } from '../utils/api.js'

const PLUGIN_ID = 'MediaRatingFiller'
const PAGE_LIMIT = 100

const STATUS_LABELS = {
  scanned: '已扫描',
  skipped_existing: '已有分级',
  queued: '待处理',
  updated_omdb: 'OMDb写入',
  updated_tmdb: 'TMDb写入',
  fallback_mainland: '大陆兜底',
  fallback_other: '其他兜底',
  no_imdbid_no_tmdbid: '无ID',
  api_limit: 'API限额',
  api_error: 'API失败',
  parse_error: '解析失败',
  write_error: '写入失败',
  manual_updated: '手动修改',
  manual_failed: '手动失败',
}

const props = defineProps({
  api: {
    type: Object,
    default: () => ({}),
  },
})

const emit = defineEmits(['action', 'switch', 'close'])

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const message = ref('')
const items = ref([])
const stats = ref({
  filtered_count: 0,
  total_count: 0,
  success_count: 0,
  failed_count: 0,
  fallback_count: 0,
  manual_count: 0,
})

const filters = reactive({
  country: '',
  new_rating: '',
  status: '',
  year: '',
  media_type: '',
})

const showEditDialog = ref(false)
const editingItem = ref(null)
const editRating = ref('')

const pluginBase = computed(() => `plugin/${PLUGIN_ID}`)

const statusItems = computed(() =>
  Object.entries(STATUS_LABELS).map(([value, title]) => ({ value, title })),
)

const mediaTypeItems = [
  { value: 'movie', title: 'movie' },
  { value: 'tvshow', title: 'tvshow' },
]

const headers = [
  { title: '标题', key: 'title', minWidth: '160px' },
  { title: '类型', key: 'media_type', width: '80px' },
  { title: '年份', key: 'year', width: '72px' },
  { title: '原分级', key: 'old_rating', width: '88px' },
  { title: '新分级', key: 'new_rating', width: '88px' },
  { title: '状态', key: 'status_label', width: '100px' },
  { title: '更新时间', key: 'updated_at', width: '150px' },
  { title: '错误', key: 'error', minWidth: '120px' },
  { title: '操作', key: 'actions', width: '96px', sortable: false },
]

const statsText = computed(() => {
  const s = stats.value
  return `筛选结果 ${s.filtered_count} 条 | 总记录 ${s.total_count} 条 | 成功 ${s.success_count} | 失败 ${s.failed_count} | 兜底 ${s.fallback_count} | 手动 ${s.manual_count} | 当前展示 ${items.value.length} 条（最多 ${PAGE_LIMIT} 条）`
})

function displayValue(value) {
  return value || '-'
}

async function loadRecords() {
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const params = {
      limit: PAGE_LIMIT,
      offset: 0,
    }
    for (const [key, value] of Object.entries(filters)) {
      const text = String(value || '').trim()
      if (text) {
        params[key] = text
      }
    }
    const response = await props.api.get(`${pluginBase.value}/records`, { params })
    const data = unwrapResponse(response) || {}
    items.value = data.items || []
    stats.value = {
      filtered_count: 0,
      total_count: 0,
      success_count: 0,
      failed_count: 0,
      fallback_count: 0,
      manual_count: 0,
      ...(data.stats || {}),
    }
    emit('action')
  } catch (err) {
    error.value = err?.message || '加载历史记录失败'
  } finally {
    loading.value = false
  }
}

function clearFilters() {
  filters.country = ''
  filters.new_rating = ''
  filters.status = ''
  filters.year = ''
  filters.media_type = ''
  loadRecords()
}

function openEditDialog(item) {
  editingItem.value = item
  editRating.value = item.new_rating || item.old_rating || ''
  showEditDialog.value = true
}

function closeEditDialog() {
  showEditDialog.value = false
  editingItem.value = null
  editRating.value = ''
}

async function saveRating() {
  if (!editingItem.value) {
    return
  }
  const rating = String(editRating.value || '').trim()
  if (!rating) {
    error.value = '分级不能为空'
    return
  }
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const response = await props.api.post(`${pluginBase.value}/records/update`, {
      id: editingItem.value.id,
      rating,
    })
    unwrapResponse(response)
    message.value = '手动修改分级成功'
    closeEditDialog()
    await loadRecords()
  } catch (err) {
    error.value = err?.message || '手动修改分级失败'
  } finally {
    saving.value = false
  }
}

onMounted(loadRecords)
</script>

<template>
  <div class="media-rating-filler-page pa-2">
    <VAlert v-if="error" type="error" variant="tonal" class="mb-3" closable @click:close="error = ''">
      {{ error }}
    </VAlert>
    <VAlert v-if="message" type="success" variant="tonal" class="mb-3" closable @click:close="message = ''">
      {{ message }}
    </VAlert>

    <VAlert type="info" variant="tonal" class="mb-4">
      {{ statsText }}
    </VAlert>

    <VCard variant="outlined" class="mb-4 pa-4">
      <div class="text-subtitle-2 mb-3">组合筛选</div>
      <VRow dense>
        <VCol cols="12" md="4" lg="2">
          <VTextField v-model="filters.country" label="国家地区" variant="outlined" density="compact" hide-details clearable />
        </VCol>
        <VCol cols="12" md="4" lg="2">
          <VTextField v-model="filters.new_rating" label="新分级" variant="outlined" density="compact" hide-details clearable />
        </VCol>
        <VCol cols="12" md="4" lg="2">
          <VSelect
            v-model="filters.status"
            :items="statusItems"
            item-title="title"
            item-value="value"
            label="处理状态"
            variant="outlined"
            density="compact"
            hide-details
            clearable
          />
        </VCol>
        <VCol cols="12" md="4" lg="2">
          <VTextField v-model="filters.year" label="年份" variant="outlined" density="compact" hide-details clearable />
        </VCol>
        <VCol cols="12" md="4" lg="2">
          <VSelect
            v-model="filters.media_type"
            :items="mediaTypeItems"
            item-title="title"
            item-value="value"
            label="类型"
            variant="outlined"
            density="compact"
            hide-details
            clearable
          />
        </VCol>
        <VCol cols="12" md="8" lg="2" class="d-flex align-center ga-2">
          <VBtn color="primary" :loading="loading" @click="loadRecords">查询</VBtn>
          <VBtn variant="outlined" :disabled="loading" @click="clearFilters">清空</VBtn>
        </VCol>
      </VRow>
    </VCard>

    <VDataTable
      :headers="headers"
      :items="items"
      :loading="loading"
      density="compact"
      class="history-table"
      items-per-page="-1"
      hide-default-footer
    >
      <template #item.old_rating="{ item }">
        {{ displayValue(item.old_rating) }}
      </template>
      <template #item.new_rating="{ item }">
        {{ displayValue(item.new_rating) }}
      </template>
      <template #item.error="{ item }">
        {{ displayValue(item.error) }}
      </template>
      <template #item.actions="{ item }">
        <VBtn size="small" variant="text" color="primary" @click="openEditDialog(item)">修改分级</VBtn>
      </template>
      <template #no-data>
        <div class="text-medium-emphasis py-6 text-center">没有符合筛选条件的历史记录</div>
      </template>
    </VDataTable>

    <VDialog v-model="showEditDialog" max-width="480">
      <VCard>
        <VCardTitle>手动修改分级</VCardTitle>
        <VCardText>
          <div class="mb-3 text-body-2">
            {{ editingItem?.title || '' }}
          </div>
          <VTextField
            v-model="editRating"
            label="新分级"
            variant="outlined"
            density="comfortable"
            placeholder="如 PG-13 / R / TV-MA"
            autofocus
            @keyup.enter="saveRating"
          />
        </VCardText>
        <VCardActions>
          <VSpacer />
          <VBtn variant="text" @click="closeEditDialog">取消</VBtn>
          <VBtn color="primary" :loading="saving" @click="saveRating">保存</VBtn>
        </VCardActions>
      </VCard>
    </VDialog>
  </div>
</template>

<style scoped>
.history-table {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
}
</style>
