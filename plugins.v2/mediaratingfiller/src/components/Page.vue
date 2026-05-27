<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { unwrapResponse } from '../utils/api.js'

const PLUGIN_ID = 'MediaRatingFiller'
const PAGE_SIZE_OPTIONS = [20, 50, 100]

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
const editingItems = ref([])
const editRating = ref('')
const selected = ref([])
const page = ref(1)
const pageSize = ref(20)

const pluginBase = computed(() => `plugin/${PLUGIN_ID}`)

const statusItems = [
  { title: '成功', value: 'success' },
  { title: '失败', value: 'failed' },
]

const RATING_OPTIONS = [
  { title: '儿童可看 (G / PG / TV-G)', value: 'G' },
  { title: '需家长陪同 (PG-13 / TV-14)', value: 'PG-13' },
  { title: '未成年禁止 (R / NC-17 / TV-MA)', value: 'R' },
]

const RATING_VALUE_ALIASES = {
  G: 'G',
  PG: 'G',
  'TV-Y': 'G',
  'TV-G': 'G',
  'TV-PG': 'G',
  'PG-13': 'PG-13',
  'TV-14': 'PG-13',
  PG12: 'PG-13',
  '12': 'PG-13',
  R: 'R',
  'NC-17': 'R',
  'TV-MA': 'R',
  '18': 'R',
  NR: 'R',
}

const mediaTypeItems = [
  { value: 'movie', title: 'movie' },
  { value: 'tvshow', title: 'tvshow' },
]

const headers = [
  { title: '标题', key: 'title', minWidth: '160px' },
  { title: '媒体路径', key: 'media_path', minWidth: '200px' },
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
  return `筛选结果 ${s.filtered_count} 条 | 总记录 ${s.total_count} 条 | 成功 ${s.success_count} | 失败 ${s.failed_count} | 兜底 ${s.fallback_count} | 手动 ${s.manual_count}`
})

const totalPages = computed(() => {
  const total = stats.value.filtered_count || 0
  if (total <= 0) {
    return 1
  }
  return Math.ceil(total / pageSize.value)
})

const pageRangeText = computed(() => {
  const total = stats.value.filtered_count || 0
  if (total <= 0) {
    return '暂无记录'
  }
  const start = (page.value - 1) * pageSize.value + 1
  const end = Math.min(page.value * pageSize.value, total)
  return `第 ${start}-${end} 条，共 ${total} 条`
})

const isBatchEdit = computed(() => editingItems.value.length > 1)

const editDialogTitle = computed(() => (isBatchEdit.value ? '批量修改分级' : '手动修改分级'))

const editDialogSummary = computed(() => {
  if (!editingItems.value.length) {
    return ''
  }
  if (isBatchEdit.value) {
    return `已选 ${editingItems.value.length} 条记录，将统一修改为所选分级`
  }
  return editingItems.value[0]?.title || ''
})

const selectedCountText = computed(() => {
  const count = selected.value.length
  if (count <= 0) {
    return ''
  }
  return `已勾选 ${count} 条（点击任意勾选行的「修改分级」可批量修改）`
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
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
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
    selected.value = []
    stats.value = {
      filtered_count: 0,
      total_count: 0,
      success_count: 0,
      failed_count: 0,
      fallback_count: 0,
      manual_count: 0,
      ...(data.stats || {}),
    }
    const maxPage = Math.max(1, Math.ceil((stats.value.filtered_count || 0) / pageSize.value) || 1)
    if (page.value > maxPage) {
      page.value = maxPage
      loading.value = false
      return loadRecords()
    }
    emit('action')
  } catch (err) {
    error.value = err?.message || '加载历史记录失败'
  } finally {
    loading.value = false
  }
}

function searchRecords() {
  page.value = 1
  loadRecords()
}

function clearFilters() {
  filters.country = ''
  filters.new_rating = ''
  filters.status = ''
  filters.year = ''
  filters.media_type = ''
  page.value = 1
  loadRecords()
}

function onPageChange(value) {
  page.value = value
  loadRecords()
}

function onPageSizeChange(value) {
  pageSize.value = Number(value) || 20
  page.value = 1
  loadRecords()
}

function resolveEditRating(raw) {
  const text = String(raw || '').trim().toUpperCase()
  if (!text) {
    return ''
  }
  const matched = RATING_OPTIONS.find((item) => item.value.toUpperCase() === text)
  if (matched) {
    return matched.value
  }
  return RATING_VALUE_ALIASES[text] || ''
}

function openEditDialog(item) {
  const selectedIds = new Set(selected.value.map((row) => row.id))
  const batchItems = selectedIds.has(item.id) && selected.value.length > 1 ? [...selected.value] : [item]
  editingItems.value = batchItems
  editRating.value = resolveEditRating(item.new_rating || item.old_rating)
  showEditDialog.value = true
}

function closeEditDialog() {
  showEditDialog.value = false
  editingItems.value = []
  editRating.value = ''
}

async function saveRating() {
  if (!editingItems.value.length) {
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
    const ids = editingItems.value.map((item) => item.id)
    const payload = ids.length > 1 ? { ids, rating } : { id: ids[0], rating }
    const response = await props.api.post(`${pluginBase.value}/records/update`, payload)
    const data = unwrapResponse(response)
    if (response?.message) {
      message.value = response.message
    } else if (ids.length > 1) {
      message.value = `批量修改成功（${ids.length} 条）`
    } else {
      message.value = '手动修改分级成功'
    }
    if (data?.failed_count > 0 && data?.errors?.length) {
      error.value = data.errors.slice(0, 3).join('；')
    } else if (data?.failed_count > 0) {
      error.value = `有 ${data.failed_count} 条记录修改失败`
    }
    closeEditDialog()
    selected.value = []
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
          <VBtn color="primary" :loading="loading" @click="searchRecords">查询</VBtn>
          <VBtn variant="outlined" :disabled="loading" @click="clearFilters">清空</VBtn>
        </VCol>
      </VRow>
    </VCard>

    <div v-if="selectedCountText" class="text-caption text-medium-emphasis mb-2 px-1">
      {{ selectedCountText }}
    </div>

    <VDataTable
      v-model="selected"
      :headers="headers"
      :items="items"
      :loading="loading"
      show-select
      item-value="id"
      return-object
      density="compact"
      class="history-table"
      items-per-page="-1"
      hide-default-footer
    >
      <template #item.media_path="{ item }">
        <span class="path-cell" :title="item.media_path || item.nfo_path || ''">
          {{ displayValue(item.media_path || item.nfo_path) }}
        </span>
      </template>
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

    <div class="d-flex align-center justify-space-between flex-wrap ga-3 mt-3 px-1">
      <div class="text-caption text-medium-emphasis">{{ pageRangeText }}</div>
      <div class="d-flex align-center ga-3 flex-wrap">
        <VSelect
          :model-value="pageSize"
          :items="PAGE_SIZE_OPTIONS"
          label="每页条数"
          variant="outlined"
          density="compact"
          hide-details
          style="min-width: 110px"
          @update:model-value="onPageSizeChange"
        />
        <VPagination
          :model-value="page"
          :length="totalPages"
          :total-visible="7"
          density="compact"
          @update:model-value="onPageChange"
        />
      </div>
    </div>

    <VDialog v-model="showEditDialog" max-width="520">
      <VCard>
        <VCardTitle>{{ editDialogTitle }}</VCardTitle>
        <VCardText>
          <div class="mb-3 text-body-2">
            {{ editDialogSummary }}
          </div>
          <div
            v-if="!isBatchEdit && (editingItems[0]?.new_rating || editingItems[0]?.old_rating)"
            class="mb-3 text-caption text-medium-emphasis"
          >
            当前分级：{{ editingItems[0]?.new_rating || editingItems[0]?.old_rating }}
          </div>
          <div v-if="isBatchEdit" class="mb-3 batch-titles text-caption text-medium-emphasis">
            <div v-for="item in editingItems.slice(0, 8)" :key="item.id">· {{ item.title || item.media_path || item.id }}</div>
            <div v-if="editingItems.length > 8">… 另有 {{ editingItems.length - 8 }} 条</div>
          </div>
          <VSelect
            v-model="editRating"
            :items="RATING_OPTIONS"
            item-title="title"
            item-value="value"
            label="选择新分级"
            variant="outlined"
            density="comfortable"
            placeholder="请选择分级"
          />
        </VCardText>
        <VCardActions>
          <VSpacer />
          <VBtn variant="text" @click="closeEditDialog">取消</VBtn>
          <VBtn color="primary" :loading="saving" @click="saveRating">
            {{ isBatchEdit ? `保存（${editingItems.length} 条）` : '保存' }}
          </VBtn>
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

.path-cell {
  display: inline-block;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.batch-titles {
  max-height: 160px;
  overflow-y: auto;
}
</style>
