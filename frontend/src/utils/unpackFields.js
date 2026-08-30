// Q7：input_fields（嵌套路径扁平列表，如 ['content', 'a.b', 'file_path']）→
// 动态表单的模板与收集。纯函数，供 Onboarding.vue 冒烟输入步使用（可单测）。

/**
 * 展开扁平路径 → 嵌套空模板 + 路径元信息。
 * @param {string[]} fields 如 ['content', 'a.b', 'file_path']
 * @returns {{ paths: Array<{path:string, leaf:string}>, template: object }}
 *   示例：paths=[{path:'content',leaf:'content'},{path:'a.b',leaf:'b'},{path:'file_path',leaf:'file_path'}]
 *         template={content:'', a:{b:''}, file_path:''}
 */
export function unpackFields(fields = []) {
  const paths = []
  const template = {}
  for (const f of [...fields].sort()) {
    const parts = f.split('.')
    paths.push({ path: f, leaf: parts[parts.length - 1] })
    let node = template
    for (const p of parts.slice(0, -1)) {
      let child = node[p]
      if (child === undefined) {
        child = {}
        node[p] = child
      } else if (typeof child !== 'object') {
        child = {}
        node[p] = child
      }
      node = child
    }
    node[parts[parts.length - 1]] = ''
  }
  return { paths, template }
}

/**
 * 扁平路径值表 → 嵌套对象（与 template 同构）。
 * @param {string[]} fields 如 ['content', 'a.b']
 * @param {Record<string,string>} values 如 {content:'你好', 'a.b':'x'}
 * @returns {object} 如 {content:'你好', a:{b:'x'}}
 */
export function collectInput(fields = [], values = {}) {
  const out = {}
  for (const f of fields) {
    const val = values[f]
    if (val === undefined || val === '') continue // 空串跳过，骨架空串补全
    const parts = f.split('.')
    let node = out
    for (const p of parts.slice(0, -1)) {
      node[p] = node[p] || {}
      node = node[p]
    }
    node[parts[parts.length - 1]] = val
  }
  return out
}
