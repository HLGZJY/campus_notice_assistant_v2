// ESLint 9 flat config
// B02.T1：前端质量门禁。error 必须清零（lint 脚本带 --max-warnings=0）。
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import vueParser from 'vue-eslint-parser'

export default [
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'src/api/types.ts', // openapi 自动生成，不参与 lint
      'src/api/schema.ts', // openapi 索引（自动导出，不 lint）
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tseslint.parser,
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    rules: {
      'vue/multi-word-component-names': 'off', // 允许单文件组件（如 Home.vue）
      'vue/no-v-html': 'off', // 富文本正文由 DOMPurify 清洗，允许 v-html
    },
  },
  {
    files: ['**/*.ts', '**/*.tsx'],
    languageOptions: {
      parser: tseslint.parser,
    },
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    // 全局规则覆盖（.vue 的 script 块也会命中此层）
    rules: {
      'no-unused-vars': 'off', // 交给 @typescript-eslint 处理（含类型）
      'no-undef': 'off', // TS/Vite 环境变量由 tsc 校验
    },
  },
]
