// context.js
// React context holding the current UI language and its string table.

import { createContext, useContext } from 'react';
import strings from './strings';

export const I18nContext = createContext({ lang: 'en', t: strings.en, setLang: () => {} });

export const useI18n = () => useContext(I18nContext);

export const LANGS = Object.keys(strings);
