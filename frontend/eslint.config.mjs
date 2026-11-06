// ESLint flat configuration: TypeScript and Angular template rules, and the browser security rule of T-SEC-09.
import eslint from '@eslint/js';
import angular from 'angular-eslint';
import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';

import { templateRestrictions, typeScriptRestrictions } from './tools/eslint/unsafe-html.mjs';

export default defineConfig(
  {
    // The fixtures break the rules on purpose; tools/eslint/unsafe-html.test.mjs lints them explicitly. The generated
    // API types are not hand-written; tools/api-types.mjs checks them against the OpenAPI document instead.
    ignores: ['dist/', 'out-tsc/', '.angular/', 'node_modules/', 'tools/eslint/fixtures/', 'src/app/core/api/generated/'],
  },
  {
    files: ['**/*.ts'],
    extends: [eslint.configs.recommended, tseslint.configs.strict, tseslint.configs.stylistic, angular.configs.tsRecommended],
    processor: angular.processInlineTemplates,
    rules: {
      // Angular components and directives may have an empty class: the decorator carries their behavior.
      '@typescript-eslint/no-extraneous-class': ['error', { allowWithDecorator: true }],
      '@angular-eslint/component-selector': ['error', { type: 'element', prefix: ['lyr', 'ob'], style: 'kebab-case' }],
      '@angular-eslint/directive-selector': ['error', { type: 'attribute', prefix: ['lyr', 'ob'], style: 'camelCase' }],
      'no-restricted-syntax': [
        'error',
        ...typeScriptRestrictions,
        {
          // tools/check-styles.mjs checks colors in .scss files only, so component styles must live there.
          selector: 'Decorator > CallExpression[callee.name="Component"] Property[key.name="styles"]',
          message: 'Put component styles in a .scss file through styleUrl, so the token color check covers them.',
        },
      ],
    },
  },
  {
    files: ['**/*.html'],
    extends: [angular.configs.templateRecommended, angular.configs.templateAccessibility],
    rules: {
      '@angular-eslint/template/no-inline-styles': 'error',
      'no-restricted-syntax': ['error', ...templateRestrictions],
    },
  },
  {
    files: ['**/*.mjs'],
    extends: [eslint.configs.recommended],
    languageOptions: {
      sourceType: 'module',
      globals: { console: 'readonly', process: 'readonly', URL: 'readonly' },
    },
  },
);
