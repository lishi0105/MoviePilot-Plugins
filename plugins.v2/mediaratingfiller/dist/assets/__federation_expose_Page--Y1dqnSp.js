import { importShared } from './__federation_fn_import-JrT3xvdd.js';

function unwrapResponse(response) {
  if (response == null) {
    return null
  }
  if (typeof response === 'object' && Object.prototype.hasOwnProperty.call(response, 'success')) {
    if (!response.success) {
      throw new Error(response.message || '请求失败')
    }
    return response.data ?? null
  }
  return response
}

const _export_sfc = (sfc, props) => {
  const target = sfc.__vccOpts || sfc;
  for (const [key, val] of props) {
    target[key] = val;
  }
  return target;
};

const {toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createVNode:_createVNode,createElementVNode:_createElementVNode,withKeys:_withKeys,createElementBlock:_createElementBlock} = await importShared('vue');


const _hoisted_1 = { class: "media-rating-filler-page pa-2" };
const _hoisted_2 = { class: "mb-3 text-body-2" };

const {computed,onMounted,reactive,ref} = await importShared('vue');

const PLUGIN_ID = 'MediaRatingFiller';
const PAGE_LIMIT = 100;


const _sfc_main = {
  __name: 'Page',
  props: {
  api: {
    type: Object,
    default: () => ({}),
  },
},
  emits: ['action', 'switch', 'close'],
  setup(__props, { emit: __emit }) {

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
};

const props = __props;

const emit = __emit;

const loading = ref(false);
const saving = ref(false);
const error = ref('');
const message = ref('');
const items = ref([]);
const stats = ref({
  filtered_count: 0,
  total_count: 0,
  success_count: 0,
  failed_count: 0,
  fallback_count: 0,
  manual_count: 0,
});

const filters = reactive({
  country: '',
  new_rating: '',
  status: '',
  year: '',
  media_type: '',
});

const showEditDialog = ref(false);
const editingItem = ref(null);
const editRating = ref('');

const pluginBase = computed(() => `plugin/${PLUGIN_ID}`);

const statusItems = computed(() =>
  Object.entries(STATUS_LABELS).map(([value, title]) => ({ value, title })),
);

const mediaTypeItems = [
  { value: 'movie', title: 'movie' },
  { value: 'tvshow', title: 'tvshow' },
];

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
];

const statsText = computed(() => {
  const s = stats.value;
  return `筛选结果 ${s.filtered_count} 条 | 总记录 ${s.total_count} 条 | 成功 ${s.success_count} | 失败 ${s.failed_count} | 兜底 ${s.fallback_count} | 手动 ${s.manual_count} | 当前展示 ${items.value.length} 条（最多 ${PAGE_LIMIT} 条）`
});

function displayValue(value) {
  return value || '-'
}

async function loadRecords() {
  loading.value = true;
  error.value = '';
  message.value = '';
  try {
    const params = {
      limit: PAGE_LIMIT,
      offset: 0,
    };
    for (const [key, value] of Object.entries(filters)) {
      const text = String(value || '').trim();
      if (text) {
        params[key] = text;
      }
    }
    const response = await props.api.get(`${pluginBase.value}/records`, { params });
    const data = unwrapResponse(response) || {};
    items.value = data.items || [];
    stats.value = {
      filtered_count: 0,
      total_count: 0,
      success_count: 0,
      failed_count: 0,
      fallback_count: 0,
      manual_count: 0,
      ...(data.stats || {}),
    };
    emit('action');
  } catch (err) {
    error.value = err?.message || '加载历史记录失败';
  } finally {
    loading.value = false;
  }
}

function clearFilters() {
  filters.country = '';
  filters.new_rating = '';
  filters.status = '';
  filters.year = '';
  filters.media_type = '';
  loadRecords();
}

function openEditDialog(item) {
  editingItem.value = item;
  editRating.value = item.new_rating || item.old_rating || '';
  showEditDialog.value = true;
}

function closeEditDialog() {
  showEditDialog.value = false;
  editingItem.value = null;
  editRating.value = '';
}

async function saveRating() {
  if (!editingItem.value) {
    return
  }
  const rating = String(editRating.value || '').trim();
  if (!rating) {
    error.value = '分级不能为空';
    return
  }
  saving.value = true;
  error.value = '';
  message.value = '';
  try {
    const response = await props.api.post(`${pluginBase.value}/records/update`, {
      id: editingItem.value.id,
      rating,
    });
    unwrapResponse(response);
    message.value = '手动修改分级成功';
    closeEditDialog();
    await loadRecords();
  } catch (err) {
    error.value = err?.message || '手动修改分级失败';
  } finally {
    saving.value = false;
  }
}

onMounted(loadRecords);

return (_ctx, _cache) => {
  const _component_VAlert = _resolveComponent("VAlert");
  const _component_VTextField = _resolveComponent("VTextField");
  const _component_VCol = _resolveComponent("VCol");
  const _component_VSelect = _resolveComponent("VSelect");
  const _component_VBtn = _resolveComponent("VBtn");
  const _component_VRow = _resolveComponent("VRow");
  const _component_VCard = _resolveComponent("VCard");
  const _component_VDataTable = _resolveComponent("VDataTable");
  const _component_VCardTitle = _resolveComponent("VCardTitle");
  const _component_VCardText = _resolveComponent("VCardText");
  const _component_VSpacer = _resolveComponent("VSpacer");
  const _component_VCardActions = _resolveComponent("VCardActions");
  const _component_VDialog = _resolveComponent("VDialog");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    (error.value)
      ? (_openBlock(), _createBlock(_component_VAlert, {
          key: 0,
          type: "error",
          variant: "tonal",
          class: "mb-3",
          closable: "",
          "onClick:close": _cache[0] || (_cache[0] = $event => (error.value = ''))
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(error.value), 1)
          ]),
          _: 1
        }))
      : _createCommentVNode("", true),
    (message.value)
      ? (_openBlock(), _createBlock(_component_VAlert, {
          key: 1,
          type: "success",
          variant: "tonal",
          class: "mb-3",
          closable: "",
          "onClick:close": _cache[1] || (_cache[1] = $event => (message.value = ''))
        }, {
          default: _withCtx(() => [
            _createTextVNode(_toDisplayString(message.value), 1)
          ]),
          _: 1
        }))
      : _createCommentVNode("", true),
    _createVNode(_component_VAlert, {
      type: "info",
      variant: "tonal",
      class: "mb-4"
    }, {
      default: _withCtx(() => [
        _createTextVNode(_toDisplayString(statsText.value), 1)
      ]),
      _: 1
    }),
    _createVNode(_component_VCard, {
      variant: "outlined",
      class: "mb-4 pa-4"
    }, {
      default: _withCtx(() => [
        _cache[11] || (_cache[11] = _createElementVNode("div", { class: "text-subtitle-2 mb-3" }, "组合筛选", -1)),
        _createVNode(_component_VRow, { dense: "" }, {
          default: _withCtx(() => [
            _createVNode(_component_VCol, {
              cols: "12",
              md: "4",
              lg: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VTextField, {
                  modelValue: filters.country,
                  "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((filters.country) = $event)),
                  label: "国家地区",
                  variant: "outlined",
                  density: "compact",
                  "hide-details": "",
                  clearable: ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              md: "4",
              lg: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VTextField, {
                  modelValue: filters.new_rating,
                  "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((filters.new_rating) = $event)),
                  label: "新分级",
                  variant: "outlined",
                  density: "compact",
                  "hide-details": "",
                  clearable: ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              md: "4",
              lg: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSelect, {
                  modelValue: filters.status,
                  "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((filters.status) = $event)),
                  items: statusItems.value,
                  "item-title": "title",
                  "item-value": "value",
                  label: "处理状态",
                  variant: "outlined",
                  density: "compact",
                  "hide-details": "",
                  clearable: ""
                }, null, 8, ["modelValue", "items"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              md: "4",
              lg: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VTextField, {
                  modelValue: filters.year,
                  "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((filters.year) = $event)),
                  label: "年份",
                  variant: "outlined",
                  density: "compact",
                  "hide-details": "",
                  clearable: ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              md: "4",
              lg: "2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VSelect, {
                  modelValue: filters.media_type,
                  "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((filters.media_type) = $event)),
                  items: mediaTypeItems,
                  "item-title": "title",
                  "item-value": "value",
                  label: "类型",
                  variant: "outlined",
                  density: "compact",
                  "hide-details": "",
                  clearable: ""
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCol, {
              cols: "12",
              md: "8",
              lg: "2",
              class: "d-flex align-center ga-2"
            }, {
              default: _withCtx(() => [
                _createVNode(_component_VBtn, {
                  color: "primary",
                  loading: loading.value,
                  onClick: loadRecords
                }, {
                  default: _withCtx(() => [...(_cache[9] || (_cache[9] = [
                    _createTextVNode("查询", -1)
                  ]))]),
                  _: 1
                }, 8, ["loading"]),
                _createVNode(_component_VBtn, {
                  variant: "outlined",
                  disabled: loading.value,
                  onClick: clearFilters
                }, {
                  default: _withCtx(() => [...(_cache[10] || (_cache[10] = [
                    _createTextVNode("清空", -1)
                  ]))]),
                  _: 1
                }, 8, ["disabled"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }),
    _createVNode(_component_VDataTable, {
      headers: headers,
      items: items.value,
      loading: loading.value,
      density: "compact",
      class: "history-table",
      "items-per-page": "-1",
      "hide-default-footer": ""
    }, {
      "item.old_rating": _withCtx(({ item }) => [
        _createTextVNode(_toDisplayString(displayValue(item.old_rating)), 1)
      ]),
      "item.new_rating": _withCtx(({ item }) => [
        _createTextVNode(_toDisplayString(displayValue(item.new_rating)), 1)
      ]),
      "item.error": _withCtx(({ item }) => [
        _createTextVNode(_toDisplayString(displayValue(item.error)), 1)
      ]),
      "item.actions": _withCtx(({ item }) => [
        _createVNode(_component_VBtn, {
          size: "small",
          variant: "text",
          color: "primary",
          onClick: $event => (openEditDialog(item))
        }, {
          default: _withCtx(() => [...(_cache[12] || (_cache[12] = [
            _createTextVNode("修改分级", -1)
          ]))]),
          _: 1
        }, 8, ["onClick"])
      ]),
      "no-data": _withCtx(() => [...(_cache[13] || (_cache[13] = [
        _createElementVNode("div", { class: "text-medium-emphasis py-6 text-center" }, "没有符合筛选条件的历史记录", -1)
      ]))]),
      _: 1
    }, 8, ["items", "loading"]),
    _createVNode(_component_VDialog, {
      modelValue: showEditDialog.value,
      "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((showEditDialog).value = $event)),
      "max-width": "480"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_VCard, null, {
          default: _withCtx(() => [
            _createVNode(_component_VCardTitle, null, {
              default: _withCtx(() => [...(_cache[14] || (_cache[14] = [
                _createTextVNode("手动修改分级", -1)
              ]))]),
              _: 1
            }),
            _createVNode(_component_VCardText, null, {
              default: _withCtx(() => [
                _createElementVNode("div", _hoisted_2, _toDisplayString(editingItem.value?.title || ''), 1),
                _createVNode(_component_VTextField, {
                  modelValue: editRating.value,
                  "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((editRating).value = $event)),
                  label: "新分级",
                  variant: "outlined",
                  density: "comfortable",
                  placeholder: "如 PG-13 / R / TV-MA",
                  autofocus: "",
                  onKeyup: _withKeys(saveRating, ["enter"])
                }, null, 8, ["modelValue"])
              ]),
              _: 1
            }),
            _createVNode(_component_VCardActions, null, {
              default: _withCtx(() => [
                _createVNode(_component_VSpacer),
                _createVNode(_component_VBtn, {
                  variant: "text",
                  onClick: closeEditDialog
                }, {
                  default: _withCtx(() => [...(_cache[15] || (_cache[15] = [
                    _createTextVNode("取消", -1)
                  ]))]),
                  _: 1
                }),
                _createVNode(_component_VBtn, {
                  color: "primary",
                  loading: saving.value,
                  onClick: saveRating
                }, {
                  default: _withCtx(() => [...(_cache[16] || (_cache[16] = [
                    _createTextVNode("保存", -1)
                  ]))]),
                  _: 1
                }, 8, ["loading"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }, 8, ["modelValue"])
  ]))
}
}

};
const Page = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-a4d6a0ec"]]);

export { Page as default };
