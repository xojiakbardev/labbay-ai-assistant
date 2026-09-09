<script setup lang="ts">
import type { Product, Variant } from "~/types/api";
import { ApiError } from "~/composables/useApi";
import {
  ArrowLeft,
  Save,
  Plus,
  Trash2,
  Image as ImageIcon,
  Check,
  AlertCircle,
  Package,
  Layers,
  Info,
  Sparkles,
  X,
  Settings2,
  Camera,
  Star,
  Upload,
} from "lucide-vue-next";

// Shadcn Vue UI Components
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectSeparator,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

const props = defineProps<{
  mode: "create" | "edit";
  productId?: string;
}>();

const api = useMivoApi();
const router = useRouter();
const { t } = useI18n();

const loading = ref(props.mode === "edit");
const saving = ref(false);
const error = ref<string | null>(null);

// Form Fields
const name = ref("");
const description = ref("");
const price = ref<string | number>("");
const currency = ref("UZS");
const category = ref("Poyabzal");
const availability = ref(true);
const imageUrl = ref("");
const selectedImageFile = ref<File | null>(null);
const imagePreviewUrl = ref<string | null>(null);

// Attributes
const material = ref("");
const fit = ref("");
const gender = ref("unisex");
const aiInstructions = ref("");

// Variants
const variants = ref<Variant[]>([]);

// Categories State & Management
const defaultCategories = [
  "Poyabzal",
  "Futbolka",
  "Hoodie",
  "Shim",
  "Kiyim",
  "Aksessuar",
  "Parfyumeriya",
  "Elektronika",
  "Boshqa"
];

const availableCategories = ref<string[]>([...defaultCategories]);
const isAddCategoryModalOpen = ref(false);
const newCategoryInput = ref("");

function loadStoredCategories() {
  try {
    const raw = localStorage.getItem("mivo_custom_categories");
    if (raw) {
      const parsed: string[] = JSON.parse(raw);
      for (const c of parsed) {
        if (c && !availableCategories.value.includes(c)) {
          const otherIdx = availableCategories.value.indexOf("Boshqa");
          if (otherIdx !== -1) {
            availableCategories.value.splice(otherIdx, 0, c);
          } else {
            availableCategories.value.push(c);
          }
        }
      }
    }
  } catch {}
}

function saveCategoryToStorage(cat: string) {
  try {
    const raw = localStorage.getItem("mivo_custom_categories");
    const list: string[] = raw ? JSON.parse(raw) : [];
    if (!list.includes(cat)) {
      list.push(cat);
      localStorage.setItem("mivo_custom_categories", JSON.stringify(list));
    }
  } catch {}
}

function openAddCategoryModal() {
  newCategoryInput.value = "";
  isAddCategoryModalOpen.value = true;
}

function confirmAddCategory() {
  const trimmed = newCategoryInput.value.trim();
  if (!trimmed) return;
  if (!availableCategories.value.includes(trimmed)) {
    const otherIdx = availableCategories.value.indexOf("Boshqa");
    if (otherIdx !== -1) {
      availableCategories.value.splice(otherIdx, 0, trimmed);
    } else {
      availableCategories.value.push(trimmed);
    }
    saveCategoryToStorage(trimmed);
  }
  category.value = trimmed;
  newCategoryInput.value = "";
  isAddCategoryModalOpen.value = false;
}

function addVariantRow(color: string = "", size: string = "") {
  const c = color.trim();
  const s = size.trim();
  const val = c && s ? `${c} / ${s}` : c || s || "";
  const attrs: Record<string, any> = {};
  if (c) attrs.color = c;
  if (s) attrs.size = s;

  variants.value.push({
    variant_type: "combination",
    color: c,
    size: s,
    value: val,
    attributes: attrs,
    sku: null,
    barcode: null,
    price_override: null,
    stock_quantity: null,
    image_url: null,
    images: [],
    new_image_url: "",
    availability: true,
  } as any);
}

function updateVariantValue(v: any) {
  const c = (v.color || "").trim();
  const s = (v.size || "").trim();
  if (c && s) {
    v.value = `${c} / ${s}`;
  } else {
    v.value = c || s || v.value || "";
  }
  if (!v.attributes) v.attributes = {};
  if (c) v.attributes.color = c;
  if (s) v.attributes.size = s;
}

function removeVariantRow(index: number) {
  variants.value.splice(index, 1);
}

function generateSkuCode(c: string = "", s: string = ""): string {
  const base = (name.value || "PROD")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w.substring(0, 3).toUpperCase())
    .join("-");
  const cCode = c.trim() ? c.trim().substring(0, 3).toUpperCase() : "";
  const sCode = s.trim() ? s.trim().toUpperCase() : "";
  return [base, cCode, sCode].filter(Boolean).join("-");
}

function autoGenerateAllSkus() {
  for (const v of variants.value as any[]) {
    if (!v.sku || !v.sku.trim()) {
      v.sku = generateSkuCode(v.color, v.size);
    }
  }
}

function parseImageUrls(raw: string): string[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];

  // If base64 data URI, NEVER split by comma (data URI always has a comma separating header and data)
  if (trimmed.startsWith("data:")) {
    return [trimmed];
  }

  // Split by newlines
  const lines = trimmed.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  const result: string[] = [];

  for (const line of lines) {
    if (line.startsWith("data:")) {
      result.push(line);
      continue;
    }
    // If line has multiple http/https URLs
    if ((line.match(/https?:\/\//g) || []).length > 1) {
      const parts = line
        .split(/(?=https?:\/\/)/g)
        .map((s) => s.replace(/^[,\s]+|[,\s]+$/g, ""))
        .filter(Boolean);
      result.push(...parts);
    } else {
      const cleaned = line.replace(/^[,\s]+|[,\s]+$/g, "");
      if (cleaned) result.push(cleaned);
    }
  }

  return result;
}

function addSkuImage(v: any) {
  const raw = (v.new_image_url || "").trim();
  if (!raw) return;
  const parts = parseImageUrls(raw);
  if (!v.images) v.images = [];
  for (const p of parts) {
    if (!v.images.includes(p)) {
      v.images.push(p);
    }
  }
  if (!v.image_url && v.images.length > 0) {
    v.image_url = v.images[0];
  }
  v.new_image_url = "";
}

function onVariantFileSelect(e: Event, v: any) {
  const target = e.target as HTMLInputElement;
  if (!target.files || target.files.length === 0) return;
  const files = Array.from(target.files);
  for (const file of files) {
    const reader = new FileReader();
    reader.onload = (event) => {
      const dataUrl = event.target?.result as string;
      if (dataUrl) {
        if (!v.images) v.images = [];
        v.images.push(dataUrl);
        if (!v.image_url) {
          v.image_url = dataUrl;
        }
      }
    };
    reader.readAsDataURL(file);
  }
  target.value = "";
}

function setPrimarySkuImage(v: any, index: number) {
  if (!v.images || !v.images[index]) return;
  const selected = v.images[index];
  v.images.splice(index, 1);
  v.images.unshift(selected);
  v.image_url = selected;
}

function removeSkuImage(v: any, index: number) {
  if (!v.images) return;
  const removed = v.images.splice(index, 1)[0];
  if (v.image_url === removed) {
    v.image_url = v.images.length > 0 ? v.images[0] : null;
  }
}

function onFileSelect(e: Event) {
  const target = e.target as HTMLInputElement;
  if (target.files && target.files.length > 0) {
    const file = target.files[0];
    selectedImageFile.value = file;
    imagePreviewUrl.value = URL.createObjectURL(file);
  }
}

function clearImage() {
  selectedImageFile.value = null;
  imagePreviewUrl.value = null;
  imageUrl.value = "";
}

// Live displayed image
const displayImage = computed(() => {
  if (imagePreviewUrl.value) return imagePreviewUrl.value;
  if (imageUrl.value.trim()) return imageUrl.value.trim();
  return null;
});

// Load existing product if edit mode
onMounted(async () => {
  loadStoredCategories();
  if (props.mode === "edit" && props.productId) {
    try {
      loading.value = true;
      const p = await api.getProduct(props.productId);
      name.value = p.name || "";
      description.value = p.description || "";
      price.value = p.price !== null && p.price !== undefined ? p.price : "";
      currency.value = p.currency || "UZS";
      availability.value = p.availability ?? true;

      const pCat = p.category || (p.attributes?.category as string) || "Poyabzal";
      if (!availableCategories.value.includes(pCat)) {
        availableCategories.value.unshift(pCat);
      }
      category.value = pCat;

      if (p.attributes) {
        material.value = (p.attributes.material as string) || "";
        fit.value = (p.attributes.fit as string) || "";
        gender.value = (p.attributes.gender as string) || "unisex";
        aiInstructions.value = (p.attributes.ai_instructions as string) || (p.attributes.ai_notes as string) || "";
        if (p.attributes.image_url) {
          imageUrl.value = p.attributes.image_url as string;
        }
      }

      if (p.images && p.images.length > 0) {
        imageUrl.value = p.images[0].url;
      }

      variants.value = (p.variants || []).map((v) => {
        const attrs = (v.attributes || {}) as Record<string, any>;
        let color = attrs.color ? String(attrs.color).trim() : "";
        let size = attrs.size ? String(attrs.size).trim() : "";
        if (!color && !size && v.value) {
          if (v.value.includes(" / ")) {
            const parts = v.value.split(" / ").map((s: string) => s.trim());
            color = parts[0] || "";
            size = parts[1] || "";
          } else {
            color = v.value;
          }
        }
        return {
          id: v.id,
          variant_type: v.variant_type || "combination",
          color,
          size,
          value: v.value || (color && size ? `${color} / ${size}` : color || size || ""),
          attributes: attrs,
          sku: v.sku || null,
          barcode: v.barcode || null,
          price_override: v.price_override ?? null,
          stock_quantity: v.stock_quantity ?? null,
          image_url: v.image_url || (v.images && v.images.length > 0 ? v.images[0] : null),
          images: v.images && v.images.length > 0 ? [...v.images] : (v.image_url ? [v.image_url] : []),
          new_image_url: "",
          availability: v.availability ?? true,
        } as any;
      });
    } catch (err) {
      error.value = t("productForm.fetchError");
    } finally {
      loading.value = false;
    }
  }
});

function normalizedVariants(): Variant[] {
  return (variants.value as any[])
    .filter((v) => (v.color && v.color.trim()) || (v.size && v.size.trim()) || (v.value && v.value.trim()))
    .map((v) => {
      const color = (v.color || "").trim();
      const size = (v.size || "").trim();
      let val = (v.value || "").trim();
      if (color && size) {
        val = `${color} / ${size}`;
      } else if (color) {
        val = color;
      } else if (size) {
        val = size;
      }

      const attrs: Record<string, any> = { ...(v.attributes || {}) };
      if (color) attrs.color = color;
      if (size) attrs.size = size;

      if (v.new_image_url && v.new_image_url.trim()) {
        const extra = parseImageUrls(v.new_image_url);
        if (!v.images) v.images = [];
        for (const e of extra) {
          if (!v.images.includes(e)) v.images.push(e);
        }
      }

      const effectiveImages = (v.images || (v.image_url ? [v.image_url] : [])).filter((img: any) => typeof img === "string" && img.trim().length > 0);
      const primaryImage = v.image_url?.trim() || (effectiveImages.length > 0 ? effectiveImages[0] : null);
      if (primaryImage && effectiveImages.includes(primaryImage)) {
        const pIdx = effectiveImages.indexOf(primaryImage);
        if (pIdx > 0) {
          effectiveImages.splice(pIdx, 1);
          effectiveImages.unshift(primaryImage);
        }
      } else if (primaryImage && !effectiveImages.includes(primaryImage)) {
        effectiveImages.unshift(primaryImage);
      }

      return {
        variant_type: "combination",
        value: val || "Standart",
        attributes: attrs,
        sku: v.sku?.trim() || null,
        barcode: v.barcode?.trim() || null,
        price_override: v.price_override ? Number(v.price_override) : null,
        stock_quantity:
          v.stock_quantity === null || v.stock_quantity === undefined || String(v.stock_quantity) === ""
            ? null
            : Number(v.stock_quantity),
        image_url: primaryImage,
        images: effectiveImages,
        availability: v.availability,
      };
    });
}

async function onSubmit() {
  if (!name.value.trim()) {
    error.value = t("productForm.productNameRequired");
    return;
  }

  error.value = null;
  saving.value = true;

  const finalCategory = category.value || "Poyabzal";
  const effectiveImageUrl = imageUrl.value.trim();

  const attributesPayload: Record<string, any> = {
    category: finalCategory,
  };
  if (effectiveImageUrl) attributesPayload.image_url = effectiveImageUrl;
  if (material.value.trim()) attributesPayload.material = material.value.trim();
  if (fit.value.trim()) attributesPayload.fit = fit.value.trim();
  if (gender.value.trim()) attributesPayload.gender = gender.value.trim();
  if (aiInstructions.value.trim()) attributesPayload.ai_instructions = aiInstructions.value.trim();

  const imagesPayload = effectiveImageUrl
    ? [{ url: effectiveImageUrl, is_primary: true }]
    : [];

  try {
    if (props.mode === "create") {
      const created = await api.createProduct({
        name: name.value.trim(),
        description: description.value.trim() || null,
        price: price.value !== "" && price.value !== null ? Number(price.value) : null,
        currency: currency.value,
        category: finalCategory,
        availability: availability.value,
        variants: normalizedVariants(),
        attributes: attributesPayload,
        images: imagesPayload as any,
      });

      if (selectedImageFile.value && created.id) {
        try {
          await api.uploadProductImage(created.id, selectedImageFile.value);
        } catch (e) {
          console.warn("Rasm yuklashda xatolik:", e);
        }
      }

      router.push("/products");
    } else if (props.mode === "edit" && props.productId) {
      await api.updateProduct(props.productId, {
        name: name.value.trim(),
        description: description.value.trim() || null,
        price: price.value !== "" && price.value !== null ? Number(price.value) : null,
        currency: currency.value,
        category: finalCategory,
        availability: availability.value,
        variants: normalizedVariants(),
        attributes: attributesPayload,
        images: imagesPayload as any,
      });

      if (selectedImageFile.value) {
        try {
          await api.uploadProductImage(props.productId, selectedImageFile.value);
        } catch (e) {
          console.warn("Rasm yuklashda xatolik:", e);
        }
      }

      router.push("/products");
    }
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t("productForm.saveError");
  } finally {
    saving.value = false;
  }
}

function handleKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === "s") {
    e.preventDefault();
    onSubmit();
  }
}

onMounted(() => {
  window.addEventListener("keydown", handleKeydown);
});

onUnmounted(() => {
  window.removeEventListener("keydown", handleKeydown);
});
</script>

<template>
  <div class="product-page-container w-full pb-16 space-y-6">
    <!-- Top Action Bar -->
    <div class="flex items-center justify-between gap-4 pb-4 border-b border-border">
      <div class="flex items-center gap-2.5">
        <Button
          variant="outline"
          size="icon"
          class="h-9 w-9"
          :title="t('productForm.back')"
          @click="router.push('/products')"
        >
          <ArrowLeft :size="18" />
        </Button>
        <h1 class="text-xl font-bold tracking-tight text-foreground">
          {{ mode === "create" ? t("productForm.newTitle") : t("productForm.editTitle") }}
        </h1>
      </div>

      <div class="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          class="h-9 px-4"
          @click="router.push('/products')"
        >
          {{ t("productForm.cancel") }}
        </Button>
        <Button
          variant="default"
          size="sm"
          class="h-9 px-5 gap-2 font-semibold"
          :disabled="saving || loading || !name.trim()"
          @click="onSubmit"
        >
          <Save v-if="!saving" :size="16" />
          <div v-else class="h-4 w-4 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin"></div>
          <span>{{ saving ? t("productForm.saving") : t("productForm.save") }}</span>
        </Button>
      </div>
    </div>

    <!-- Alert / Errors -->
    <div v-if="error" class="p-3.5 rounded-lg bg-destructive/10 border border-destructive/20 text-destructive text-sm flex items-center gap-2.5">
      <AlertCircle :size="16" class="shrink-0" />
      <span>{{ error }}</span>
    </div>

    <!-- Loading Skeleton -->
    <div v-if="loading" class="space-y-4">
      <Skeleton class="h-40 rounded-xl w-full" />
      <Skeleton class="h-40 rounded-xl w-full" />
    </div>

    <!-- Form Content (Single Column) -->
    <div v-else class="space-y-5">

      <!-- 1. Asosiy ma'lumotlar Card -->
      <Card class="p-5 space-y-4">
        <div class="flex items-center justify-between pb-2 border-b border-border">
          <div class="flex items-center gap-2">
            <Package :size="18" class="text-primary" />
            <h2 class="text-sm font-semibold text-foreground">{{ t("productForm.basicInfo") }}</h2>
          </div>
          <!-- Compact Sotuvda mavjud switch -->
          <label class="flex items-center gap-2 cursor-pointer">
            <Switch v-model:checked="availability" />
            <span class="text-xs font-semibold text-foreground">{{ t("productForm.inStock") }}</span>
          </label>
        </div>

        <!-- Mahsulot nomi -->
        <div class="space-y-1.5">
          <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            {{ t("productForm.productName") }} <span class="text-destructive">*</span>
          </Label>
          <Input
            v-model="name"
            :placeholder="t('productForm.productNamePlaceholder')"
            required
          />
        </div>

        <!-- Kategoriya & Narxi & Valyuta (1 Row) -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <!-- Kategoriya -->
          <div class="space-y-1.5">
            <div class="flex items-center justify-between">
              <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                {{ t("productForm.category") }}
              </Label>
              <button
                type="button"
                class="text-xs font-semibold text-primary hover:underline flex items-center gap-1 cursor-pointer"
                @click="openAddCategoryModal"
              >
                <Plus :size="13" />
                <span>{{ t("productForm.newCategory") }}</span>
              </button>
            </div>
            <Select v-model="category">
              <SelectTrigger class="w-full h-9">
                <SelectValue :placeholder="t('productForm.selectCategory')" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem v-for="cat in availableCategories" :key="cat" :value="cat">
                    {{ cat }}
                  </SelectItem>
                </SelectGroup>
                <SelectSeparator />
                <div
                  class="relative flex w-full cursor-pointer select-none items-center rounded-sm py-1.5 px-2 text-xs font-semibold text-primary hover:bg-muted outline-none gap-1.5 transition-colors"
                  @click="openAddCategoryModal"
                >
                  <Plus :size="13" />
                  <span>{{ t("productForm.addNewCategoryEllipsis") }}</span>
                </div>
              </SelectContent>
            </Select>
          </div>

          <!-- Narxi va Valyuta -->
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {{ t("productForm.priceAndCurrency") }}
            </Label>
            <div class="flex gap-2">
              <Input
                v-model="price"
                type="number"
                step="any"
                class="flex-1"
                placeholder="149000"
              />
              <Select v-model="currency">
                <SelectTrigger class="w-24 h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="UZS">UZS</SelectItem>
                  <SelectItem value="USD">USD</SelectItem>
                  <SelectItem value="RUB">RUB</SelectItem>
                  <SelectItem value="EUR">EUR</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <!-- Tavsif -->
        <div class="space-y-1.5">
          <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            {{ t("productForm.description") }}
          </Label>
          <Textarea
            v-model="description"
            rows="2"
            :placeholder="t('productForm.descPlaceholder')"
          />
        </div>
      </Card>

      <!-- 2. Mahsulot rasmi Card (Compact row) -->
      <Card class="p-5 space-y-3">
        <div class="flex items-center justify-between pb-2 border-b border-border">
          <div class="flex items-center gap-2">
            <ImageIcon :size="18" class="text-primary" />
            <h2 class="text-sm font-semibold text-foreground">{{ t("productForm.imageSection") }}</h2>
          </div>
          <Button
            v-if="displayImage"
            variant="ghost"
            size="sm"
            class="h-6 text-xs text-destructive hover:text-destructive px-2"
            @click="clearImage"
          >
            {{ t("productForm.clearImage") }}
          </Button>
        </div>

        <div class="flex flex-col sm:flex-row gap-4 items-stretch">
          <!-- Thumbnail Preview -->
          <div class="relative w-28 min-h-[116px] rounded-xl overflow-hidden bg-muted/40 border border-border flex items-center justify-center shrink-0">
            <img
              v-if="displayImage"
              :src="displayImage"
              :alt="t('productForm.imageSection')"
              class="w-full h-full object-cover"
            />
            <ImageIcon v-else :size="32" class="text-muted-foreground/40" />
          </div>

          <!-- URL and Upload Inputs -->
          <div class="flex-1 space-y-2.5 w-full flex flex-col justify-between">
            <div class="space-y-1">
              <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                {{ t("productForm.imageUrlLabel") }}
              </Label>
              <Input
                v-model="imageUrl"
                type="url"
                placeholder="https://example.com/rasm.jpg"
              />
            </div>
            <div class="space-y-1">
              <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                {{ t("productForm.orUploadFile") }}
              </Label>
              <input
                type="file"
                accept="image/*"
                class="w-full text-xs text-muted-foreground file:mr-2 file:py-1 file:px-2.5 file:rounded-md file:border-0 file:text-xs file:font-medium file:bg-muted file:text-foreground hover:file:bg-muted/80 cursor-pointer"
                @change="onFileSelect"
              />
            </div>
          </div>
        </div>
      </Card>

      <!-- 3. Variantlar va Ombor Qoldiqlari (SKU) Card -->
      <Card class="p-5 space-y-4">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-border">
          <div class="flex items-center gap-2">
            <Layers :size="18" class="text-primary" />
            <div>
              <div class="flex items-center gap-2">
                <h2 class="text-sm font-semibold text-foreground">{{ t("productForm.variantsAndStock") }}</h2>
                <span class="text-xs px-2 py-0.5 rounded-full bg-primary/10 font-bold text-primary">
                  {{ variants.length }} {{ t("products.variants") }}
                </span>
              </div>
            </div>
          </div>

          <div class="flex items-center gap-2">
            <Button
              v-if="variants.length > 0"
              type="button"
              variant="outline"
              size="sm"
              class="h-8 text-xs gap-1.5 cursor-pointer"
              @click="autoGenerateAllSkus"
              :title="t('productForm.autoSkuTitle')"
            >
              <Sparkles :size="13" class="text-amber-500" />
              <span>{{ t("productForm.autoSku") }}</span>
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              class="h-8 text-xs gap-1.5 cursor-pointer font-semibold shadow-xs"
              @click="addVariantRow()"
            >
              <Plus :size="14" />
              <span>{{ t("productForm.addVariant") }}</span>
            </Button>
          </div>
        </div>

        <!-- VARIANTLAR RO'YXATI -->
        <div
          v-if="variants.length === 0"
          class="text-center py-10 px-4 rounded-xl border border-dashed border-border bg-muted/10 space-y-3"
        >
          <Package :size="32" class="mx-auto text-muted-foreground/40" />
          <div class="space-y-1">
            <p class="text-sm font-semibold text-foreground">{{ t("productForm.noVariantsTitle") }}</p>
            <p class="text-xs text-muted-foreground max-w-sm mx-auto">
              {{ t("productForm.noVariantsDesc") }}
            </p>
          </div>
          <div class="pt-2 flex items-center justify-center gap-2">
            <Button
              type="button"
              variant="default"
              size="sm"
              class="h-9 px-4 text-xs font-semibold gap-1.5 cursor-pointer shadow-sm"
              @click="addVariantRow()"
            >
              <Plus :size="14" />
              <span>{{ t("productForm.addVariant") }}</span>
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              class="h-9 px-3 text-xs gap-1.5 cursor-pointer"
              @click="addVariantRow('Qora', 'M')"
            >
              <Plus :size="13" />
              <span>{{ t("productForm.sampleVariant") }}</span>
            </Button>
          </div>
        </div>

        <div v-else class="space-y-3">
          <div
            v-for="(v, idx) in variants"
            :key="idx"
            class="p-4 rounded-xl border border-border bg-background hover:border-primary/40 transition-all space-y-3 shadow-xs"
          >
            <!-- Card Header: Title / Number, Thumbnail Preview, Status and Delete -->
            <div class="flex items-center justify-between gap-3 pb-2.5 border-b border-border/60">
              <div class="flex items-center gap-3">
                <!-- Thumbnail Preview (Square 40x40px) -->
                <div class="relative h-10 w-10 rounded-lg border border-border bg-muted/30 overflow-hidden flex items-center justify-center shrink-0">
                  <img
                    v-if="v.image_url || (v.images && v.images.length > 0)"
                    :src="v.image_url || v.images[0]"
                    class="h-full w-full object-cover"
                    :alt="t('productForm.variantImages')"
                  />
                  <Camera v-else :size="16" class="text-muted-foreground/50" />
                </div>

                <div>
                  <div class="text-xs font-semibold text-foreground flex items-center gap-1.5">
                    <span class="px-1.5 py-0.5 rounded bg-muted text-[10px] font-mono font-bold text-muted-foreground">
                      #{{ idx + 1 }}
                    </span>
                    <span>
                      {{ (v as any).color || (v as any).size ? [(v as any).color, (v as any).size].filter(Boolean).join(" • ") : t("productForm.newVariant") }}
                    </span>
                  </div>
                  <div class="text-[11px] text-muted-foreground font-mono mt-0.5">
                    {{ v.sku ? `${t("productForm.skuLabel")}: ${v.sku}` : t("productForm.skuNotEntered") }}
                  </div>
                </div>
              </div>

              <!-- Actions: Availability and Delete -->
              <div class="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  class="h-7 text-xs px-2.5 gap-1.5 cursor-pointer font-medium"
                  :class="v.availability ? 'text-emerald-600 bg-emerald-500/10 border-emerald-500/20 hover:bg-emerald-500/20' : 'text-muted-foreground hover:bg-muted'"
                  :title="v.availability ? t('productForm.inStockShort') : t('productForm.outOfStockShort')"
                  @click="v.availability = !v.availability"
                >
                  <Check v-if="v.availability" :size="12" />
                  <X v-else :size="12" />
                  <span>{{ v.availability ? t("productForm.inStockShort") : t("productForm.outOfStockShort") }}</span>
                </Button>

                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  class="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer"
                  :title="t('common.delete')"
                  @click="removeVariantRow(idx)"
                >
                  <Trash2 :size="14" />
                </Button>
              </div>
            </div>

            <!-- Main Input Fields (Responsive Grid) -->
            <div class="grid grid-cols-2 sm:grid-cols-6 gap-2.5">
              <!-- Rang (Color) -->
              <div class="space-y-1">
                <Label class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {{ t("productForm.color") }}
                </Label>
                <Input
                  v-model="(v as any).color"
                  placeholder="Masalan: Qora"
                  class="h-8 text-xs font-medium"
                  @input="updateVariantValue(v)"
                />
              </div>

              <!-- O'lcham (Size) -->
              <div class="space-y-1">
                <Label class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {{ t("productForm.size") }}
                </Label>
                <Input
                  v-model="(v as any).size"
                  placeholder="Masalan: M yoki 42"
                  class="h-8 text-xs font-medium"
                  @input="updateVariantValue(v)"
                />
              </div>

              <!-- Ombor qoldig'i (Soni) -->
              <div class="space-y-1">
                <Label class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {{ t("productForm.stockQty") }}
                </Label>
                <Input
                  v-model="v.stock_quantity"
                  type="number"
                  min="0"
                  :placeholder="t('productForm.unlimited')"
                  class="h-8 text-xs font-bold"
                />
              </div>

              <!-- Maxsus narx (UZS) -->
              <div class="space-y-1">
                <Label class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {{ t("productForm.specialPrice") }}
                </Label>
                <Input
                  v-model="v.price_override"
                  type="number"
                  min="0"
                  :placeholder="t('productForm.basePrice')"
                  class="h-8 text-xs"
                />
              </div>

              <!-- Shtrix-kod (Barcode) -->
              <div class="space-y-1">
                <Label class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {{ t("productForm.barcode") }}
                </Label>
                <Input
                  v-model="v.barcode"
                  placeholder="478001234567"
                  class="h-8 text-xs font-mono"
                />
              </div>

              <!-- Artikul (SKU) -->
              <div class="space-y-1">
                <Label class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {{ t("productForm.sku") }}
                </Label>
                <Input
                  v-model="v.sku"
                  placeholder="HD-BLK-M"
                  class="h-8 text-xs font-mono"
                />
              </div>
            </div>

            <!-- Variant Rasmlari -->
            <div class="pt-2.5 border-t border-border/40 space-y-2">
              <div class="flex items-center justify-between">
                <Label class="text-[11px] font-semibold text-muted-foreground flex items-center gap-1.5">
                  <Camera :size="13" class="text-primary" />
                  <span>{{ t("productForm.variantImages") }}</span>
                  <span v-if="v.images && v.images.length > 0" class="text-[10px] text-muted-foreground font-normal">
                    ({{ v.images.length }} — {{ t("productForm.variantImagesHelp") }})
                  </span>
                </Label>
              </div>

              <!-- Rasm URL qo'shish inputi va Fayl yuklash tugmasi -->
              <div class="flex items-center gap-2">
                <Input
                  v-model="v.new_image_url"
                  :placeholder="t('productForm.variantImageUrlPlaceholder')"
                  class="h-8 text-xs font-mono flex-1"
                  @keydown.enter.prevent="addSkuImage(v)"
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  class="h-8 px-3 text-xs gap-1 cursor-pointer shrink-0 font-medium"
                  :disabled="!v.new_image_url || !v.new_image_url.trim()"
                  @click="addSkuImage(v)"
                >
                  <Plus :size="13" />
                  <span>{{ t("productForm.add") }}</span>
                </Button>

                <label
                  class="h-8 px-3 text-xs gap-1.5 cursor-pointer shrink-0 font-medium inline-flex items-center justify-center rounded-md border border-input bg-background hover:bg-accent hover:text-accent-foreground transition-colors shadow-xs"
                  :title="t('productForm.pickFromDevice')"
                >
                  <Upload :size="13" class="text-primary" />
                  <span class="hidden sm:inline">{{ t("productForm.selectFile") }}</span>
                  <input
                    type="file"
                    accept="image/*"
                    multiple
                    class="hidden"
                    @change="onVariantFileSelect($event, v)"
                  />
                </label>
              </div>

              <!-- Rasm miniatyuralari -->
              <div v-if="v.images && v.images.length > 0" class="flex flex-wrap items-center gap-2 pt-0.5">
                <div
                  v-for="(url, imgIdx) in v.images"
                  :key="imgIdx"
                  class="group relative h-14 w-14 rounded-lg border overflow-hidden transition-all bg-muted/20 shrink-0"
                  :class="imgIdx === 0 ? 'border-primary ring-2 ring-primary/25 shadow-xs' : 'border-border hover:border-foreground/40'"
                >
                  <img :src="url" class="h-full w-full object-cover" />

                  <!-- 1-Asosiy belgisi -->
                  <div
                    v-if="imgIdx === 0"
                    class="absolute top-0 left-0 bg-primary text-primary-foreground text-[8px] font-bold px-1 py-0.5 rounded-br shadow-xs"
                  >
                    {{ t("productForm.primaryBadge") }}
                  </div>

                  <!-- Boshqa rasmni 1-o'ringa o'tkazish tugmasi -->
                  <button
                    v-else
                    type="button"
                    class="absolute inset-0 bg-black/65 opacity-0 group-hover:opacity-100 flex flex-col items-center justify-center text-white text-[9px] font-semibold transition-opacity cursor-pointer text-center p-0.5"
                    :title="t('productForm.makePrimaryTitle')"
                    @click="setPrimarySkuImage(v, imgIdx)"
                  >
                    <Star :size="12" class="text-amber-400 fill-amber-400 mb-0.5" />
                    <span>{{ t("productForm.makePrimary") }}</span>
                  </button>

                  <!-- Rasmni o'chirish -->
                  <button
                    type="button"
                    class="absolute top-0.5 right-0.5 h-4 w-4 rounded-full bg-background/90 hover:bg-destructive hover:text-white text-muted-foreground flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer shadow-xs"
                    :title="t('common.delete')"
                    @click="removeSkuImage(v, imgIdx)"
                  >
                    <X :size="10" />
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- Pastki tugma: Yangi variant qo'shish -->
          <div class="flex items-center justify-between pt-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              class="h-9 px-4 text-xs gap-1.5 cursor-pointer font-medium hover:border-primary hover:text-primary transition-all"
              @click="addVariantRow()"
            >
              <Plus :size="14" />
              <span>{{ t("productForm.addVariant") }}</span>
            </Button>

            <span class="text-xs text-muted-foreground">
              {{ t("productForm.totalVariants") }}: <b class="text-foreground font-semibold">{{ variants.length }}</b>
            </span>
          </div>
        </div>
      </Card>

      <!-- 4. Qo'shimcha parametrlar Card -->
      <Card class="p-5 space-y-4">
        <div class="flex items-center gap-2 pb-2 border-b border-border">
          <Info :size="18" class="text-primary" />
          <h2 class="text-sm font-semibold text-foreground">{{ t("productForm.extraParams") }}</h2>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {{ t("productForm.material") }}
            </Label>
            <Input
              v-model="material"
              placeholder="100% paxta, charm..."
            />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {{ t("productForm.fit") }}
            </Label>
            <Input
              v-model="fit"
              placeholder="Oversize, Regular..."
            />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {{ t("productForm.gender") }}
            </Label>
            <Select v-model="gender">
              <SelectTrigger class="w-full h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unisex">{{ t("productForm.genderUnisex") }}</SelectItem>
                <SelectItem value="men">{{ t("productForm.genderMen") }}</SelectItem>
                <SelectItem value="women">{{ t("productForm.genderWomen") }}</SelectItem>
                <SelectItem value="kids">{{ t("productForm.genderKids") }}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <!-- AI uchun ko'rsatma / eslatma -->
        <div class="space-y-1.5 pt-1">
          <div class="flex items-center gap-1.5">
            <Sparkles :size="14" class="text-primary" />
            <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {{ t("productForm.aiInstructions") }}
            </Label>
          </div>
          <Textarea
            v-model="aiInstructions"
            rows="2"
            :placeholder="t('productForm.aiInstructionsPlaceholder')"
          />
        </div>
      </Card>

      <!-- Bottom Save Action Bar -->
      <div class="flex items-center justify-end gap-2 pt-2">
        <Button
          variant="outline"
          size="default"
          class="h-9 px-5"
          @click="router.push('/products')"
        >
          {{ t("productForm.cancel") }}
        </Button>
        <Button
          variant="default"
          size="default"
          class="h-9 px-6 gap-2 font-semibold"
          :disabled="saving || loading || !name.trim()"
          @click="onSubmit"
        >
          <Save v-if="!saving" :size="16" />
          <div v-else class="h-4 w-4 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin"></div>
          <span>{{ saving ? t("productForm.saving") : t("productForm.save") }}</span>
        </Button>
      </div>

    </div>

    <!-- SHADCN DIALOG: ADD CATEGORY MODAL -->
    <Dialog :open="isAddCategoryModalOpen" @update:open="isAddCategoryModalOpen = $event">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{{ t("productForm.addCategoryModalTitle") }}</DialogTitle>
        </DialogHeader>

        <form @submit.prevent="confirmAddCategory" class="space-y-4 pt-2">
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              {{ t("productForm.categoryNameLabel") }}
            </Label>
            <Input
              v-model="newCategoryInput"
              type="text"
              :placeholder="t('productForm.categoryNamePlaceholder')"
              required
              autofocus
            />
          </div>

          <DialogFooter class="gap-2 sm:gap-0 pt-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              @click="isAddCategoryModalOpen = false"
            >
              {{ t("productForm.cancel") }}
            </Button>
            <Button
              type="submit"
              variant="default"
              size="sm"
              class="gap-1.5 font-semibold"
              :disabled="!newCategoryInput.trim()"
            >
              <Plus :size="13" />
              <span>{{ t("productForm.add") }}</span>
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>

  </div>
</template>
