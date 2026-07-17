import { createContext, useContext } from "react";
import { FONT_CHOICES, THEME_DEFAULT_FONT_ID, type FontChoice } from "./fonts";
import { BUILTIN_THEMES, defaultTheme } from "./presets";
import type { DashboardTheme, ThemeListEntry } from "./types";

export interface ThemeContextValue {
  availableThemes: ThemeListEntry[];
  setTheme: (name: string) => void;
  theme: DashboardTheme;
  themeName: string;
  fontId: string;
  fontChoices: FontChoice[];
  setFont: (id: string) => void;
}

export const ThemeContext = createContext<ThemeContextValue>({
  theme: defaultTheme,
  themeName: "default",
  availableThemes: Object.values(BUILTIN_THEMES).map((theme) => ({
    name: theme.name,
    label: theme.label,
    description: theme.description,
  })),
  setTheme: () => {},
  fontId: THEME_DEFAULT_FONT_ID,
  fontChoices: FONT_CHOICES,
  setFont: () => {},
});

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}