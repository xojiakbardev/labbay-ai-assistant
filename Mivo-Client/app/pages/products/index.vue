<script setup lang="ts">
import { toast } from "vue-sonner";
import type { Product, ProductInput } from "~/types/api";
import {
  Plus,
  Pencil,
  Trash2,
  LayoutGrid,
  List,
  Sparkles,
  FileText,
  UploadCloud,
  Code,
  Package,
  ChevronDown,
  Search,
  Image as ImageIcon,
  X,
  SlidersHorizontal,
  Check,
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  AlertCircle,
  RefreshCw
} from "@lucide/vue";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "~/components/ui/select";
import MobileDrawer from "~/components/MobileDrawer.vue";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();
const route = useRoute();
const router = useRouter();

const products = ref<Product[]>([]);
const loading = ref(true);
const searchQuery = ref("");

const { productsViewMode, setProductsViewMode } = useUserPreferences();

// Card / Table view mode backed by persistent backend preference + localStorage
const viewMode = computed<"grid" | "table">({
  get() {
    return productsViewMode.value || "grid";
  },
  set(val: "grid" | "table") {
    setProductsViewMode(val);
  },
});

// Dropdown & AI Import Modal state
const showAddDropdown = ref(false);
const showAiImportModal = ref(false);
const importTab = ref<"text" | "file" | "json">("text");

// AI import form state
const rawText = ref("");
const jsonText = ref("");
const uploadedFile = ref<File | null>(null);
const fileContent = ref("");
const aiImporting = ref(false);
const aiError = ref<string | null>(null);

// Import is two steps: extract (or parse JSON) -> the owner reviews/edits
// the rows -> only then confirm. Nothing is saved before the confirm.
const MAX_IMPORT_TEXT = 50000;
const importStep = ref<"input" | "review">("input");
interface ReviewRow {
  key: number;
  // Everything the extraction produced (variants, attributes, images) is
  // kept; the review edits name/price/currency on top.
  source: ProductInput;
  name: string;
  price: string | number;
  currency: string;
}
const reviewRows = ref<ReviewRow[]>([]);
const confirmingImport = ref(false);

const loadError = ref<string | null>(null);

async function reload() {
  loading.value = true;
  loadError.value = null;
  try {
    products.value = await api.listProducts();
  } catch (err) {
    console.error("Failed to load products", err);
    loadError.value = err instanceof Error && err.message ? err.message : t("products.loadError");
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  if (route.query.view) {
    const nextQuery = { ...route.query };
    delete nextQuery.view;
    router.replace({ query: Object.keys(nextQuery).length ? nextQuery : undefined });
  }
  await reload();
});

function getProductCategory(p: Product): string {
  return (p.attributes?.category as string) || "Boshqa";
}

// Category & Availability filters
const selectedCategory = ref("all");
const selectedAvailability = ref<"all" | "available" | "out">("all");
const showMobileFilterDrawer = ref(false);

const availableCategories = computed(() => {
  const set = new Set<string>();
  for (const p of products.value) {
    const cat = getProductCategory(p);
    if (cat && cat.trim()) set.add(cat.trim());
  }
  return Array.from(set);
});

const activeFiltersCount = computed(() => {
  let count = 0;
  if (selectedCategory.value !== "all") count++;
  if (selectedAvailability.value !== "all") count++;
  return count;
});

function resetFilters() {
  selectedCategory.value = "all";
  selectedAvailability.value = "all";
  searchQuery.value = "";
}

const filteredProducts = computed(() => {
  let list = [...products.value];

  if (selectedCategory.value !== "all") {
    list = list.filter((p) => getProductCategory(p) === selectedCategory.value);
  }

  if (selectedAvailability.value === "available") {
    list = list.filter((p) => p.availability !== false);
  } else if (selectedAvailability.value === "out") {
    list = list.filter((p) => p.availability === false);
  }

  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase().trim();
    list = list.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description && p.description.toLowerCase().includes(q)) ||
        ((p.attributes?.category as string) && (p.attributes.category as string).toLowerCase().includes(q))
    );
  }

  return list;
});

// Pagination state
const currentPage = ref(1);
const pageSize = ref(8);
const pageSizeOptions = [8, 12, 24, 48];

const totalPages = computed(() => Math.max(1, Math.ceil(filteredProducts.value.length / pageSize.value)));

const paginatedProducts = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredProducts.value.slice(start, start + pageSize.value);
});

const visiblePages = computed(() => {
  const total = totalPages.value;
  const current = currentPage.value;
  if (total <= 5) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  let start = Math.max(1, current - 2);
  let end = Math.min(total, start + 4);
  if (end - start < 4) {
    start = Math.max(1, end - 4);
  }
  const pages: number[] = [];
  for (let i = start; i <= end; i++) {
    pages.push(i);
  }
  return pages;
});

watch([searchQuery, selectedCategory, selectedAvailability, pageSize], () => {
  currentPage.value = 1;
});

function getProductImage(p: Product): string | null {
  const first = p.images?.[0];
  if (first) return first.url;
  if (p.attributes?.image_url) return p.attributes.image_url as string;
  return null;
}

function formatPrice(amount: number | null, curr = "UZS"): string {
  if (amount === null || amount === undefined) return "—";
  return `${amount.toLocaleString()} ${curr}`;
}

const togglingId = ref<string | null>(null);

async function onToggleAvailability(p: Product) {
  togglingId.value = p.id;
  try {
    const updated = await api.updateProduct(p.id, { availability: !p.availability });
    const idx = products.value.findIndex((x) => x.id === p.id);
    if (idx !== -1) products.value[idx] = { ...products.value[idx]!, availability: updated.availability };
  } catch (err) {
    console.error("Failed to toggle availability", err);
    toast.error(t("products.toggleError"));
  } finally {
    togglingId.value = null;
  }
}

async function onDelete(id: string) {
  if (!confirm(t("products.deleteConfirm"))) return;
  try {
    await api.deleteProduct(id);
  } catch (err) {
    console.error("Failed to delete product", err);
    toast.error(t("products.deleteError"));
    return;
  }
  await reload();
}

function openAiImport() {
  showAddDropdown.value = false;
  aiError.value = null;
  rawText.value = "";
  jsonText.value = "";
  uploadedFile.value = null;
  fileContent.value = "";
  importTab.value = "text";
  importStep.value = "input";
  reviewRows.value = [];
  showAiImportModal.value = true;
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) return;
  uploadedFile.value = file;
  fileContent.value = "";
  aiError.value = null;

  const reader = new FileReader();
  reader.onload = (evt) => {
    fileContent.value = (evt.target?.result as string) || "";
  };
  reader.onerror = () => {
    aiError.value = t("products.fileReadError");
  };
  reader.readAsText(file);
}

function parseJsonProducts(text: string): ProductInput[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error(t("products.importJsonInvalid"));
  }
  const list = Array.isArray(parsed) ? parsed : [parsed];
  if (!list.every((item) => item && typeof item === "object" && !Array.isArray(item))) {
    throw new Error(t("products.importJsonInvalid"));
  }
  return list as ProductInput[];
}

async function extractWithAi(text: string): Promise<ProductInput[]> {
  if (text.length > MAX_IMPORT_TEXT) {
    throw new Error(t("products.importTooLong", { max: MAX_IMPORT_TEXT }));
  }
  return (await api.previewImport(text)).products;
}

function toReviewRows(items: ProductInput[]): ReviewRow[] {
  return items.map((item, idx) => ({
    key: idx,
    source: item,
    name: typeof item.name === "string" ? item.name : "",
    price: item.price === null || item.price === undefined ? "" : item.price,
    currency: typeof item.currency === "string" && item.currency ? item.currency : "UZS",
  }));
}

// Step 1: turn the input into rows to review. Nothing is written yet.
async function onAiImportSubmit() {
  aiError.value = null;
  aiImporting.value = true;

  try {
    let items: ProductInput[];
    if (importTab.value === "json") {
      if (!jsonText.value.trim()) throw new Error(t("products.importJsonEmpty"));
      items = parseJsonProducts(jsonText.value);
    } else if (importTab.value === "file") {
      if (!fileContent.value.trim()) throw new Error(t("products.importFileEmpty"));
      items = uploadedFile.value?.name.toLowerCase().endsWith(".json")
        ? parseJsonProducts(fileContent.value)
        : await extractWithAi(fileContent.value);
    } else {
      if (!rawText.value.trim()) throw new Error(t("products.importTextEmpty"));
      items = await extractWithAi(rawText.value);
    }

    if (items.length === 0) throw new Error(t("products.importNothingFound"));
    reviewRows.value = toReviewRows(items);
    importStep.value = "review";
  } catch (err) {
    aiError.value = err instanceof Error && err.message ? err.message : t("products.importError");
  } finally {
    aiImporting.value = false;
  }
}

function variantCount(row: ReviewRow): number {
  return Array.isArray(row.source.variants) ? row.source.variants.length : 0;
}

function removeReviewRow(key: number) {
  reviewRows.value = reviewRows.value.filter((r) => r.key !== key);
}

function reviewProblem(): string | null {
  if (reviewRows.value.length === 0) return t("products.importNothingToSave");
  for (const row of reviewRows.value) {
    if (!row.name.trim()) return t("products.importNameRequired");
    if (String(row.price).trim() !== "") {
      const n = Number(row.price);
      if (!Number.isFinite(n) || n < 0) return t("products.importPriceInvalid", { name: row.name.trim() });
    }
    if (row.currency.trim().length < 3) return t("products.importCurrencyInvalid", { name: row.name.trim() });
  }
  return null;
}

// Step 2: the owner confirmed — save all rows together (all-or-nothing).
async function onConfirmImport() {
  if (confirmingImport.value) return;
  const problem = reviewProblem();
  if (problem) {
    aiError.value = problem;
    return;
  }
  aiError.value = null;
  confirmingImport.value = true;
  try {
    const products = reviewRows.value.map<ProductInput>((row) => ({
      ...row.source,
      name: row.name.trim(),
      price: String(row.price).trim() === "" ? null : Number(row.price),
      currency: row.currency.trim().toUpperCase(),
    }));
    const saved = await api.confirmImport(products);
    showAiImportModal.value = false;
    toast.success(t("products.importSaved", { count: saved.length }));
    await reload();
  } catch (err) {
    aiError.value = err instanceof Error && err.message ? err.message : t("products.importError");
  } finally {
    confirmingImport.value = false;
  }
}
</script>

<template>
  <div class="products-page pb-12 space-y-3">
    <!-- Filters, Search & Actions Bar -->
    <div class="rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-xs">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <!-- Search Input + Mobile Filter Drawer Trigger -->
        <div class="flex items-center gap-2 flex-1 max-w-md w-full">
          <div class="relative flex-1">
            <Search :size="15" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
            <Input
              v-model="searchQuery"
              type="text"
              :placeholder="t('products.searchPlaceholder')"
              class="w-full h-9 pl-9 pr-3 text-sm bg-background"
            />
          </div>

          <!-- Mobile Filter Button -->
          <Button
            variant="outline"
            size="sm"
            class="md:hidden h-9 px-3 gap-1.5 shrink-0 cursor-pointer shadow-none"
            :class="{ 'border-primary text-primary bg-primary/5': activeFiltersCount > 0 }"
            @click="showMobileFilterDrawer = true"
          >
            <SlidersHorizontal :size="14" />
            <span class="text-xs font-semibold">{{ t("products.filters") }}</span>
            <span
              v-if="activeFiltersCount > 0"
              class="w-5 h-5 -mr-1 rounded-full bg-primary text-primary-foreground text-[10px] font-bold flex items-center justify-center"
            >
              {{ activeFiltersCount }}
            </span>
          </Button>

          <!-- Mobile View Switcher & Add Button -->
          <div class="flex md:hidden items-center gap-1.5 shrink-0">
            <div class="inline-flex rounded-lg bg-muted/60 p-0.5 border border-border">
              <button
                type="button"
                class="p-1.5 rounded-md transition-all cursor-pointer"
                :class="viewMode === 'grid' ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'"
                @click="viewMode = 'grid'"
              >
                <LayoutGrid :size="14" />
              </button>
              <button
                type="button"
                class="p-1.5 rounded-md transition-all cursor-pointer"
                :class="viewMode === 'table' ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'"
                @click="viewMode = 'table'"
              >
                <List :size="14" />
              </button>
            </div>

            <Button
              variant="default"
              size="sm"
              class="h-9 w-9 p-0 cursor-pointer"
              @click="router.push('/products/new')"
            >
              <Plus :size="15" />
            </Button>
          </div>
        </div>

        <!-- Desktop Controls: Category + Availability + View Mode + Add Button -->
        <div class="hidden md:flex items-center gap-2.5 shrink-0">
          <!-- Category Select -->
          <Select v-model="selectedCategory">
            <SelectTrigger class="h-8 text-xs min-w-[145px] bg-background">
              <SelectValue :placeholder="t('products.allCategories')" />
            </SelectTrigger>
            <SelectContent align="end">
              <SelectItem value="all">{{ t("products.allCategories") }}</SelectItem>
              <SelectItem v-for="cat in availableCategories" :key="cat" :value="cat">
                {{ cat }}
              </SelectItem>
            </SelectContent>
          </Select>

          <!-- Availability Select -->
          <Select v-model="selectedAvailability">
            <SelectTrigger class="h-8 text-xs min-w-[125px] bg-background">
              <SelectValue :placeholder="t('products.allStatuses')" />
            </SelectTrigger>
            <SelectContent align="end">
              <SelectItem value="all">{{ t("products.allStatuses") }}</SelectItem>
              <SelectItem value="available">{{ t("products.onlyAvailable") }}</SelectItem>
              <SelectItem value="out">{{ t("products.onlyOut") }}</SelectItem>
            </SelectContent>
          </Select>

          <div class="h-4 w-px bg-border my-auto mx-0.5" />

          <!-- View Mode Switcher -->
          <div class="inline-flex rounded-lg bg-muted/60 p-0.5 border border-border shrink-0">
            <button
              type="button"
              class="p-1 rounded-md transition-all cursor-pointer"
              :class="viewMode === 'grid' ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'"
              :title="t('products.gridTooltip')"
              @click="viewMode = 'grid'"
            >
              <LayoutGrid :size="15" />
            </button>
            <button
              type="button"
              class="p-1 rounded-md transition-all cursor-pointer"
              :class="viewMode === 'table' ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground'"
              :title="t('products.tableTooltip')"
              @click="viewMode = 'table'"
            >
              <List :size="15" />
            </button>
          </div>

          <!-- Add Button + Dropdown -->
          <div class="relative shrink-0">
            <div class="inline-flex rounded-md shadow-xs">
              <Button
                variant="default"
                size="sm"
                class="h-8 gap-1.5 rounded-r-none text-xs cursor-pointer"
                @click="router.push('/products/new')"
              >
                <Plus :size="14" />
                <span>{{ t("products.add") }}</span>
              </Button>
              <Button
                variant="default"
                size="sm"
                class="h-8 px-2 rounded-l-none border-l border-primary-foreground/20 cursor-pointer"
                @click="showAddDropdown = !showAddDropdown"
              >
                <ChevronDown :size="12" />
              </Button>
            </div>

            <!-- Backdrop to close on click outside -->
            <div
              v-if="showAddDropdown"
              class="fixed inset-0 z-30"
              @click="showAddDropdown = false"
            />

            <!-- Dropdown Menu -->
            <div
              v-if="showAddDropdown"
              class="absolute right-0 mt-1.5 w-52 p-1 z-40 rounded-xl border border-border bg-popover text-popover-foreground shadow-lg animate-in fade-in zoom-in-95 duration-100 flex flex-col"
            >
              <button
                type="button"
                class="w-full px-2.5 py-2 text-left text-xs text-foreground hover:bg-muted rounded-lg flex items-center gap-2.5 transition-colors cursor-pointer"
                @click="showAddDropdown = false; router.push('/products/new')"
              >
                <Plus :size="14" class="text-primary" />
                <span class="font-medium">{{ t("products.manualAdd") }}</span>
              </button>

              <div class="h-px bg-border my-0.5 -mx-1" />

              <button
                type="button"
                class="w-full px-2.5 py-2 text-left text-xs text-foreground hover:bg-muted rounded-lg flex items-center gap-2.5 transition-colors cursor-pointer"
                @click="openAiImport"
              >
                <Sparkles :size="14" class="text-amber-500" />
                <span class="font-medium">{{ t("products.universalAiImport") }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Mobile Active Filter Pills -->
      <div v-if="activeFiltersCount > 0" class="flex md:hidden flex-wrap items-center gap-1.5 mt-2.5 pt-2 border-t border-border/60">
        <span class="text-[11px] text-muted-foreground font-medium mr-1">{{ t("products.activeFilterLabel") }}</span>
        <span
          v-if="selectedCategory !== 'all'"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-primary/10 text-primary border border-primary/20"
        >
          <span>{{ selectedCategory }}</span>
          <button type="button" class="cursor-pointer" @click="selectedCategory = 'all'"><X :size="11" /></button>
        </span>
        <span
          v-if="selectedAvailability !== 'all'"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-500 border border-emerald-500/20"
        >
          <span>{{ selectedAvailability === 'available' ? t('products.available') : t('products.outOfStock') }}</span>
          <button type="button" class="cursor-pointer" @click="selectedAvailability = 'all'"><X :size="11" /></button>
        </span>
        <button
          type="button"
          class="text-[11px] text-muted-foreground hover:text-foreground underline ml-auto cursor-pointer"
          @click="resetFilters"
        >
          {{ t("common.clear") }}
        </button>
      </div>
    </div>

    <!-- Mobile Products Filter Drawer -->
    <MobileDrawer
      v-model:open="showMobileFilterDrawer"
      :title="t('products.filterDrawerTitle')"
      :description="t('products.filterDrawerDesc')"
    >
      <template #title-icon>
        <SlidersHorizontal :size="16" class="text-primary" />
      </template>

      <!-- Category Section -->
      <div class="space-y-2">
        <label class="text-xs font-bold text-muted-foreground uppercase tracking-wider">
          {{ t("products.category") }}
        </label>
        <div class="grid grid-cols-2 gap-2">
          <button
            type="button"
            class="p-2.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all cursor-pointer"
            :class="selectedCategory === 'all' ? 'border-primary bg-primary/10 text-primary shadow-xs' : 'border-border bg-card text-muted-foreground hover:border-border/80'"
            @click="selectedCategory = 'all'"
          >
            <span>{{ t("products.all") }}</span>
            <Check v-if="selectedCategory === 'all'" :size="13" />
          </button>
          <button
            v-for="cat in availableCategories"
            :key="cat"
            type="button"
            class="p-2.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all cursor-pointer"
            :class="selectedCategory === cat ? 'border-primary bg-primary/10 text-primary shadow-xs' : 'border-border bg-card text-muted-foreground hover:border-border/80'"
            @click="selectedCategory = cat"
          >
            <span class="truncate">{{ cat }}</span>
            <Check v-if="selectedCategory === cat" :size="13" />
          </button>
        </div>
      </div>

      <!-- Availability Section -->
      <div class="space-y-2">
        <label class="text-xs font-bold text-muted-foreground uppercase tracking-wider">
          {{ t("products.availabilityStatus") }}
        </label>
        <div class="space-y-1.5">
          <button
            v-for="opt in [
              { value: 'all', label: t('products.allProducts') },
              { value: 'available', label: t('products.onlyInStockOpt') },
              { value: 'out', label: t('products.onlyOutOpt') }
            ]"
            :key="opt.value"
            type="button"
            class="w-full p-3 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all cursor-pointer"
            :class="selectedAvailability === opt.value ? 'border-primary bg-primary/10 text-primary' : 'border-border bg-card text-foreground hover:border-border/80'"
            @click="selectedAvailability = opt.value as any"
          >
            <span>{{ opt.label }}</span>
            <Check v-if="selectedAvailability === opt.value" :size="14" class="text-primary" />
          </button>
        </div>
      </div>

      <template #footer>
        <Button
          variant="outline"
          class="flex-1 h-11 text-xs cursor-pointer shadow-none"
          @click="resetFilters"
        >
          {{ t("common.clear") }}
        </Button>
        <Button
          variant="default"
          class="flex-2 h-11 text-xs font-bold cursor-pointer shadow-xs"
          @click="showMobileFilterDrawer = false"
        >
          {{ t("products.viewResults", { count: filteredProducts.length }) }}
        </Button>
      </template>
    </MobileDrawer>

    <!-- Loading Skeleton -->
    <div v-if="loading" class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2.5 sm:gap-3">
      <Skeleton v-for="i in 8" :key="i" class="h-56 sm:h-64 rounded-xl w-full" />
    </div>

    <!-- Load Error -->
    <Card v-else-if="loadError" class="text-center py-10 px-4 border-destructive/30 shadow-xs space-y-3">
      <AlertCircle :size="28" class="mx-auto text-destructive" />
      <p class="text-sm font-semibold text-foreground">{{ t("products.loadError") }}</p>
      <p class="text-xs text-muted-foreground">{{ loadError }}</p>
      <Button variant="outline" size="sm" class="h-8 text-xs gap-1.5 cursor-pointer" @click="reload">
        <RefreshCw :size="13" />
        <span>{{ t("common.retry") }}</span>
      </Button>
    </Card>

    <!-- Empty State -->
    <Card
      v-else-if="filteredProducts.length === 0"
      class="text-center py-12 px-4 border-border shadow-xs"
    >
      <Package :size="40" class="mx-auto text-muted-foreground/40 mb-2" />
      <h3 class="text-sm font-semibold text-foreground">
        {{ searchQuery ? t("products.notFound") : t("products.noProductsTitle") }}
      </h3>
      <div v-if="!searchQuery" class="flex items-center justify-center gap-2 mt-4">
        <Button
          variant="default"
          size="sm"
          class="h-8 text-xs gap-1.5 cursor-pointer shadow-xs"
          @click="router.push('/products/new')"
        >
          <Plus :size="13" />
          <span>{{ t("products.manualAdd") }}</span>
        </Button>
        <Button
          variant="outline"
          size="sm"
          class="h-8 text-xs gap-1.5 cursor-pointer shadow-none"
          @click="openAiImport"
        >
          <Sparkles :size="13" class="text-amber-500" />
          <span>{{ t("products.universalAiImport") }}</span>
        </Button>
      </div>
    </Card>

    <!-- GRID VIEW -->
    <div
      v-else-if="viewMode === 'grid'"
      class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2.5 sm:gap-3"
    >
      <Card
        v-for="p in paginatedProducts"
        :key="p.id"
        class="group overflow-hidden flex flex-col justify-between hover:border-primary/40 transition-all shadow-2xs p-0 pt-0 gap-0"
      >
        <!-- Product Image -->
        <div class="relative w-full aspect-square bg-muted/30 overflow-hidden cursor-pointer" @click="router.push(`/products/${p.id}`)">
          <img
            v-if="getProductImage(p)"
            :src="getProductImage(p)!"
            :alt="p.name"
            class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            loading="lazy"
          />
          <div v-else class="w-full h-full flex flex-col items-center justify-center text-muted-foreground/40">
            <ImageIcon :size="28" class="sm:w-8 sm:h-8" />
          </div>

          <!-- Category Badge -->
          <div class="absolute top-1.5 left-1.5 sm:top-2 sm:left-2 max-w-[55%] truncate">
            <span class="text-[9px] sm:text-[10px] px-1.5 sm:px-2 py-0.5 rounded bg-black/60 backdrop-blur-md text-white font-medium truncate block">
              {{ getProductCategory(p) }}
            </span>
          </div>

          <!-- Availability Badge -->
          <div class="absolute top-1.5 right-1.5 sm:top-2 sm:right-2">
            <button
              type="button"
              class="text-[9px] sm:text-[10px] px-1.5 sm:px-2 py-0.5 rounded font-semibold backdrop-blur-md transition-all cursor-pointer"
              :class="p.availability ? 'bg-emerald-500/80 text-white' : 'bg-rose-500/80 text-white'"
              :disabled="togglingId === p.id"
              @click.stop="onToggleAvailability(p)"
            >
              {{ t(p.availability ? "products.available" : "products.outOfStock") }}
            </button>
          </div>
        </div>

        <!-- Details -->
        <div class="p-2.5 sm:p-3.5 flex-1 flex flex-col justify-between space-y-2 sm:space-y-2.5">
          <div>
            <h3
              class="font-semibold text-xs sm:text-sm text-foreground line-clamp-1 group-hover:text-primary transition-colors cursor-pointer"
              :title="p.name"
              @click="router.push(`/products/${p.id}`)"
            >
              {{ p.name }}
            </h3>
            <p v-if="p.description" class="text-[11px] sm:text-xs text-muted-foreground line-clamp-1 sm:line-clamp-2 mt-0.5">
              {{ p.description }}
            </p>
          </div>

          <!-- Variants Preview Chips -->
          <div v-if="p.variants && p.variants.length > 0" class="flex flex-wrap gap-1">
            <span
              v-for="v in p.variants.slice(0, 3)"
              :key="v.id || v.value"
              class="inline-flex items-center gap-1 text-[9px] sm:text-[10px] pl-1 pr-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono"
            >
              <img
                v-if="v.image_url || (v.images && v.images.length > 0)"
                :src="v.image_url || v.images![0]"
                class="h-2.5 w-2.5 sm:h-3 sm:w-3 rounded-full object-cover shrink-0"
              />
              <span class="truncate max-w-[48px]">{{ v.value }}</span><span v-if="v.stock_quantity !== null" class="opacity-60">:{{ v.stock_quantity }}</span>
            </span>
            <span v-if="p.variants.length > 3" class="text-[9px] sm:text-[10px] px-1 py-0.5 text-muted-foreground">
              +{{ p.variants.length - 3 }}
            </span>
          </div>

          <!-- Footer: Price & Actions -->
          <div class="pt-1.5 sm:pt-2 border-t border-border flex items-center justify-between gap-1">
            <div class="font-bold text-xs sm:text-sm text-foreground truncate">
              {{ formatPrice(p.price, p.currency) }}
            </div>

            <div class="flex items-center gap-0.5 sm:gap-1 shrink-0">
              <Button
                variant="ghost"
                size="icon"
                class="h-6 w-6 sm:h-7 sm:w-7 text-muted-foreground hover:text-foreground"
                :title="t('common.edit')"
                @click="router.push(`/products/${p.id}`)"
              >
                <Pencil :size="13" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                class="h-6 w-6 sm:h-7 sm:w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                :title="t('common.delete')"
                @click="onDelete(p.id)"
              >
                <Trash2 :size="13" />
              </Button>
            </div>
          </div>
        </div>
      </Card>
    </div>

    <!-- TABLE VIEW -->
    <Card
      v-else-if="viewMode === 'table'"
      class="overflow-hidden border-border shadow-xs"
    >
      <Table>
        <TableHeader class="bg-muted/40">
          <TableRow>
            <TableHead class="py-3 px-4 w-12">{{ t("products.thImage") }}</TableHead>
            <TableHead class="py-3 px-4">{{ t("products.thName") }}</TableHead>
            <TableHead class="py-3 px-4">{{ t("products.thCategory") }}</TableHead>
            <TableHead class="py-3 px-4">{{ t("products.thPrice") }}</TableHead>
            <TableHead class="py-3 px-4">{{ t("products.thVariants") }}</TableHead>
            <TableHead class="py-3 px-4">{{ t("products.thStatus") }}</TableHead>
            <TableHead class="py-3 px-4 text-right">{{ t("products.thActions") }}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow
            v-for="p in paginatedProducts"
            :key="p.id"
            class="hover:bg-muted/30 transition-colors"
          >
            <!-- Image -->
            <TableCell class="py-2.5 px-4">
              <div class="h-9 w-9 rounded-md bg-muted/40 overflow-hidden flex items-center justify-center border border-border shrink-0">
                <img
                  v-if="getProductImage(p)"
                  :src="getProductImage(p)!"
                  :alt="p.name"
                  class="h-full w-full object-cover"
                />
                <ImageIcon v-else :size="16" class="text-muted-foreground/40" />
              </div>
            </TableCell>

            <!-- Name & Description -->
            <TableCell class="py-2.5 px-4 font-medium text-foreground">
              <div class="font-semibold line-clamp-1">{{ p.name }}</div>
              <div v-if="p.description" class="text-xs text-muted-foreground line-clamp-1">{{ p.description }}</div>
            </TableCell>

            <!-- Category -->
            <TableCell class="py-2.5 px-4 text-xs text-muted-foreground">
              <Badge variant="secondary" class="font-normal text-xs">
                {{ getProductCategory(p) }}
              </Badge>
            </TableCell>

            <!-- Price -->
            <TableCell class="py-2.5 px-4 font-bold text-foreground whitespace-nowrap font-mono">
              {{ formatPrice(p.price, p.currency) }}
            </TableCell>

            <!-- Variants -->
            <TableCell class="py-2.5 px-4 text-xs">
              <div v-if="p.variants && p.variants.length > 0" class="flex flex-wrap gap-1 max-w-xs">
                <span
                  v-for="v in p.variants.slice(0, 3)"
                  :key="v.id || v.value"
                  class="inline-flex items-center gap-1 pl-1 pr-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono text-[10px]"
                >
                  <img
                    v-if="v.image_url || (v.images && v.images.length > 0)"
                    :src="v.image_url || v.images![0]"
                    class="h-3 w-3 rounded-full object-cover shrink-0"
                  />
                  <span>{{ v.value }}</span><span v-if="v.stock_quantity !== null" class="opacity-60">:{{ v.stock_quantity }}</span>
                </span>
                <span v-if="p.variants.length > 3" class="text-[10px] text-muted-foreground">
                  +{{ p.variants.length - 3 }}
                </span>
              </div>
              <span v-else class="text-muted-foreground/40">—</span>
            </TableCell>

            <!-- Availability -->
            <TableCell class="py-2.5 px-4">
              <button
                type="button"
                class="text-[10px] px-2 py-0.5 rounded font-semibold border transition-all cursor-pointer"
                :class="p.availability ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20' : 'bg-rose-500/10 text-rose-600 border-rose-500/20'"
                :disabled="togglingId === p.id"
                @click="onToggleAvailability(p)"
              >
                {{ t(p.availability ? "products.available" : "products.outOfStock") }}
              </button>
            </TableCell>

            <!-- Actions -->
            <TableCell class="py-2.5 px-4 text-right whitespace-nowrap">
              <div class="inline-flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  class="h-7 w-7 text-muted-foreground hover:text-foreground cursor-pointer"
                  :title="t('common.edit')"
                  @click="router.push(`/products/${p.id}`)"
                >
                  <Pencil :size="14" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  class="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer"
                  :title="t('common.delete')"
                  @click="onDelete(p.id)"
                >
                  <Trash2 :size="14" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </Card>

    <!-- Pagination Controls -->
    <div
      v-if="filteredProducts.length > 0"
      class="flex flex-col sm:flex-row items-center justify-between gap-3 pt-2 pb-6 px-1 text-xs text-muted-foreground"
    >
      <div class="flex items-center gap-2 order-2 sm:order-1">
        <span>
          {{ t("products.showingPagination", {
            from: (currentPage - 1) * pageSize + 1,
            to: Math.min(currentPage * pageSize, filteredProducts.length),
            total: filteredProducts.length
          }) }}
        </span>
      </div>

      <div class="flex items-center gap-2 order-1 sm:order-2 flex-wrap justify-center">
        <!-- Page Size Selector -->
        <div class="flex items-center gap-1.5 mr-2">
          <span class="hidden sm:inline">{{ t("products.perPage") }}</span>
          <Select :model-value="String(pageSize)" @update:model-value="(val) => pageSize = Number(val)">
            <SelectTrigger class="h-8 text-xs w-[85px] bg-background">
              <SelectValue :placeholder="`${pageSize}`" />
            </SelectTrigger>
            <SelectContent align="end">
              <SelectItem v-for="size in pageSizeOptions" :key="size" :value="String(size)">
                {{ size }}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        <!-- Navigation Buttons -->
        <div class="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            class="h-8 px-2.5 gap-1 text-xs cursor-pointer"
            :disabled="currentPage <= 1"
            @click="currentPage--"
          >
            <ChevronLeft :size="14" />
            <span class="hidden sm:inline">{{ t("common.prev") }}</span>
          </Button>

          <!-- Page Numbers -->
          <div class="flex items-center gap-1">
            <button
              v-for="page in visiblePages"
              :key="page"
              type="button"
              class="h-8 min-w-[32px] px-2 rounded-md font-semibold text-xs transition cursor-pointer flex items-center justify-center"
              :class="currentPage === page ? 'bg-primary text-primary-foreground shadow-xs' : 'hover:bg-muted text-muted-foreground hover:text-foreground'"
              @click="currentPage = page"
            >
              {{ page }}
            </button>
          </div>

          <Button
            variant="outline"
            size="sm"
            class="h-8 px-2.5 gap-1 text-xs cursor-pointer"
            :disabled="currentPage >= totalPages"
            @click="currentPage++"
          >
            <span class="hidden sm:inline">{{ t("common.next") }}</span>
            <ChevronRight :size="14" />
          </Button>
        </div>
      </div>
    </div>

    <!-- AI Import Modal: step 1 input, step 2 review -> confirm -->
    <Dialog :open="showAiImportModal" @update:open="showAiImportModal = $event">
      <DialogContent class="p-0 gap-0 overflow-hidden" :class="importStep === 'review' ? 'sm:max-w-3xl' : 'sm:max-w-xl'">
        <!-- Modal Header -->
        <DialogHeader class="p-4 border-b border-border">
          <DialogTitle class="text-sm font-bold text-foreground flex items-center gap-2">
            <Sparkles :size="16" class="text-amber-500" />
            <span>{{ importStep === "review" ? t("products.importReviewTitle", { count: reviewRows.length }) : t("products.universalAiImport") }}</span>
          </DialogTitle>
        </DialogHeader>

        <!-- Review step: nothing is saved until "Save" below -->
        <div v-if="importStep === 'review'" class="p-4 space-y-3">
          <div v-if="aiError" class="p-2.5 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs">
            {{ aiError }}
          </div>
          <p class="text-xs text-muted-foreground">{{ t("products.importReviewHint") }}</p>

          <div class="max-h-[55vh] overflow-auto rounded-lg border border-border">
            <Table>
              <TableHeader class="bg-muted/40">
                <TableRow>
                  <TableHead class="py-2 px-3 min-w-[180px]">{{ t("products.thName") }}</TableHead>
                  <TableHead class="py-2 px-3 w-32">{{ t("products.thPrice") }}</TableHead>
                  <TableHead class="py-2 px-3 w-24">{{ t("productForm.currency") }}</TableHead>
                  <TableHead class="py-2 px-3 w-20 text-center">{{ t("products.thVariants") }}</TableHead>
                  <TableHead class="py-2 px-3 w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="row in reviewRows" :key="row.key">
                  <TableCell class="py-1.5 px-3">
                    <Input v-model="row.name" maxlength="255" class="h-8 text-xs" />
                  </TableCell>
                  <TableCell class="py-1.5 px-3">
                    <Input v-model="row.price" type="number" min="0" step="any" class="h-8 text-xs font-mono" />
                  </TableCell>
                  <TableCell class="py-1.5 px-3">
                    <Input v-model="row.currency" maxlength="10" class="h-8 text-xs uppercase" />
                  </TableCell>
                  <TableCell class="py-1.5 px-3 text-center text-xs font-mono text-muted-foreground">
                    {{ variantCount(row) }}
                  </TableCell>
                  <TableCell class="py-1.5 px-3 text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      class="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer"
                      :title="t('common.delete')"
                      @click="removeReviewRow(row.key)"
                    >
                      <Trash2 :size="13" />
                    </Button>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>

        <template v-else>
        <!-- Import Tabs -->
        <div class="px-4 pt-3">
          <Tabs v-model="importTab" class="w-full">
            <TabsList class="grid grid-cols-3 h-8 bg-muted/60 border border-border">
              <TabsTrigger value="text" class="text-xs h-7 gap-1.5 cursor-pointer">
                <FileText :size="13" />
                <span>{{ t("products.tabText") }}</span>
              </TabsTrigger>
              <TabsTrigger value="json" class="text-xs h-7 gap-1.5 cursor-pointer">
                <Code :size="13" />
                <span>{{ t("products.tabJson") }}</span>
              </TabsTrigger>
              <TabsTrigger value="file" class="text-xs h-7 gap-1.5 cursor-pointer">
                <UploadCloud :size="13" />
                <span>{{ t("products.tabFile") }}</span>
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        <!-- Modal Body -->
        <div class="p-4 space-y-3">
          <div v-if="aiError" class="p-2.5 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-xs">
            {{ aiError }}
          </div>

          <!-- Text Tab -->
          <div v-if="importTab === 'text'">
            <Textarea
              v-model="rawText"
              :rows="6"
              :maxlength="MAX_IMPORT_TEXT"
              class="font-mono text-xs resize-none bg-background"
              :placeholder="t('products.inputPlaceholder')"
            />
            <p class="mt-1 text-right text-[11px] text-muted-foreground font-mono">{{ rawText.length }} / {{ MAX_IMPORT_TEXT }}</p>
          </div>

          <!-- JSON Tab -->
          <div v-else-if="importTab === 'json'">
            <Textarea
              v-model="jsonText"
              :rows="8"
              class="font-mono text-xs resize-none bg-background"
              placeholder="[ { &quot;name&quot;: &quot;...&quot;, &quot;price&quot;: 149000 } ]"
            />
          </div>

          <!-- File Tab -->
          <div v-else>
            <div class="border border-dashed border-border rounded-lg p-5 text-center bg-muted/20">
              <UploadCloud :size="32" class="mx-auto text-muted-foreground/40 mb-1.5" />
              <p class="text-xs font-medium text-foreground mb-2">
                {{ uploadedFile ? uploadedFile.name : t("products.selectFilePrompt") }}
              </p>
              <input
                type="file"
                accept=".json,.txt,.csv,.md"
                class="hidden"
                id="file-upload-input"
                @change="handleFileSelect"
              />
              <label
                for="file-upload-input"
                class="inline-flex items-center gap-1 px-3 py-1 text-xs font-semibold rounded-md bg-muted text-foreground border border-border hover:bg-muted/80 cursor-pointer"
              >
                {{ t("products.chooseFile") }}
              </label>
            </div>
          </div>
        </div>

        </template>

        <!-- Modal Footer -->
        <DialogFooter class="p-3.5 bg-muted/30 border-t border-border flex items-center justify-end gap-2">
          <template v-if="importStep === 'review'">
            <Button
              variant="outline"
              size="sm"
              class="h-8 text-xs gap-1.5 cursor-pointer shadow-none mr-auto"
              :disabled="confirmingImport"
              @click="importStep = 'input'; aiError = null"
            >
              <ArrowLeft :size="13" />
              <span>{{ t("common.back") }}</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              class="h-8 text-xs cursor-pointer shadow-none"
              :disabled="confirmingImport"
              @click="showAiImportModal = false"
            >
              {{ t("common.cancel") }}
            </Button>
            <Button
              variant="default"
              size="sm"
              class="h-8 text-xs gap-1.5 font-semibold cursor-pointer shadow-xs"
              :disabled="confirmingImport || reviewRows.length === 0"
              @click="onConfirmImport"
            >
              <Check v-if="!confirmingImport" :size="13" />
              <div v-else class="h-3 w-3 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin"></div>
              <span>{{ confirmingImport ? t("products.importing") : t("products.importConfirm", { count: reviewRows.length }) }}</span>
            </Button>
          </template>
          <template v-else>
            <Button
              variant="outline"
              size="sm"
              class="h-8 text-xs cursor-pointer shadow-none"
              @click="showAiImportModal = false"
            >
              {{ t("common.cancel") }}
            </Button>
            <Button
              variant="default"
              size="sm"
              class="h-8 text-xs gap-1.5 font-semibold cursor-pointer shadow-xs"
              :disabled="aiImporting"
              @click="onAiImportSubmit"
            >
              <Sparkles v-if="!aiImporting" :size="13" />
              <div v-else class="h-3 w-3 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin"></div>
              <span>{{ aiImporting ? t("products.extracting") : t("products.startImport") }}</span>
            </Button>
          </template>
        </DialogFooter>
      </DialogContent>
    </Dialog>

  </div>
</template>
