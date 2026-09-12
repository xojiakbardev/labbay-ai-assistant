<script setup lang="ts">
import type { ImageInput, ProductImage, ProductInput, ProductPatch, VariantInput } from "~/types/api";
import { IMAGE_UPLOAD_MAX_BYTES, imageUploadProblem } from "~/lib/utils";
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
  Camera,
  Star,
  Upload,
  Loader2,
} from "@lucide/vue";

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
// The main photo: its URL (typed, uploaded, or the product's current primary
// image) — or a picked file, uploaded on save.
const imageUrl = ref("");
const selectedImageFile = ref<File | null>(null);
const imagePreviewUrl = ref<string | null>(null);

// Edit mode: the server's state, which the PATCH is computed against.
const serverImages = ref<ProductImage[]>([]);
const originalPrimaryUrl = ref<string | null>(null);
// Attribute keys this form doesn't edit (e.g. from an AI import) are kept.
const originalAttributes = ref<Record<string, any>>({});

// Attributes
const material = ref("");
const fit = ref("");
const gender = ref("unisex");
const aiInstructions = ref("");

// Variants
interface FormVariant {
  id?: string;
  variant_type: string;
  color: string;
  size: string;
  value: string;
  attributes: Record<string, any>;
  sku: string | null;
  barcode: string | null;
  price_override: string | number | null;
  stock_quantity: string | number | null;
  image_url: string | null;
  images: string[];
  new_image_url: string;
  availability: boolean;
  // Photo uploads still in flight for this variant.
  uploading: number;
}

const variants = ref<FormVariant[]>([]);
const uploadingVariantImages = computed(() => variants.value.some((v) => v.uploading > 0));

function errorText(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t("productForm.saveError");
}

function uploadProblemText(problem: "type" | "size", fileName: string): string {
  return problem === "type"
    ? t("productForm.imageTypeInvalid", { name: fileName })
    : t("productForm.imageTooLarge", { name: fileName, mb: IMAGE_UPLOAD_MAX_BYTES / (1024 * 1024) });
}

function isHttpUrl(url: string): boolean {
  return /^https?:\/\/\S+$/i.test(url);
}

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
  const attrs: Record<string, any> = {};
  if (c) attrs.color = c;
  if (s) attrs.size = s;

  variants.value.push({
    variant_type: "combination",
    color: c,
    size: s,
    value: c && s ? `${c} / ${s}` : c || s || "",
    attributes: attrs,
    sku: null,
    barcode: null,
    price_override: null,
    stock_quantity: null,
    image_url: null,
    images: [],
    new_image_url: "",
    availability: true,
    uploading: 0,
  });
}

function updateVariantValue(v: FormVariant) {
  const c = (v.color || "").trim();
  const s = (v.size || "").trim();
  if (c && s) {
    v.value = `${c} / ${s}`;
  } else {
    v.value = c || s || v.value || "";
  }
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
  for (const v of variants.value) {
    if (!v.sku || !v.sku.trim()) {
      v.sku = generateSkuCode(v.color, v.size);
    }
  }
}

function parseImageUrls(raw: string): string[] {
  const trimmed = raw.trim();
  if (!trimmed) return [];

  const lines = trimmed.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  const result: string[] = [];

  for (const line of lines) {
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

// Typed URLs must be http(s) links — the server rejects anything else
// (a photo from the device goes through the upload button instead).
// Returns false when something typed was rejected.
function addSkuImage(v: FormVariant): boolean {
  const parts = parseImageUrls(v.new_image_url || "");
  const invalid = parts.filter((p) => !isHttpUrl(p));
  for (const p of parts.filter(isHttpUrl)) {
    if (!v.images.includes(p)) v.images.push(p);
  }
  if (!v.image_url && v.images.length > 0) {
    v.image_url = v.images[0]!;
  }
  if (invalid.length > 0) {
    v.new_image_url = invalid.join("\n");
    error.value = t("productForm.imageUrlInvalid");
    return false;
  }
  v.new_image_url = "";
  return true;
}

// Device photos are uploaded right away (POST /products/media) and the
// variant stores the returned URL — never the file inlined as base64.
async function onVariantFileSelect(e: Event, v: FormVariant) {
  const target = e.target as HTMLInputElement;
  const files = Array.from(target.files ?? []);
  target.value = "";
  for (const file of files) {
    const problem = imageUploadProblem(file);
    if (problem) {
      error.value = uploadProblemText(problem, file.name);
      continue;
    }
    v.uploading++;
    try {
      const { url } = await api.uploadProductMedia(file);
      if (!v.images.includes(url)) v.images.push(url);
      if (!v.image_url) v.image_url = url;
    } catch (err) {
      error.value = t("productForm.imageUploadFailed", { name: file.name, reason: errorText(err) });
    } finally {
      v.uploading--;
    }
  }
}

function setPrimarySkuImage(v: FormVariant, index: number) {
  const selected = v.images[index];
  if (!selected) return;
  v.images.splice(index, 1);
  v.images.unshift(selected);
  v.image_url = selected;
}

function removeSkuImage(v: FormVariant, index: number) {
  const removed = v.images.splice(index, 1)[0];
  if (v.image_url === removed) {
    v.image_url = v.images.length > 0 ? v.images[0]! : null;
  }
}

function clearSelectedFile() {
  selectedImageFile.value = null;
  if (imagePreviewUrl.value) URL.revokeObjectURL(imagePreviewUrl.value);
  imagePreviewUrl.value = null;
}

function onFileSelect(e: Event) {
  const target = e.target as HTMLInputElement;
  const file = target.files?.[0];
  target.value = "";
  if (!file) return;
  const problem = imageUploadProblem(file);
  if (problem) {
    error.value = uploadProblemText(problem, file.name);
    return;
  }
  clearSelectedFile();
  // The picked file replaces the current main photo when saved.
  imageUrl.value = "";
  selectedImageFile.value = file;
  imagePreviewUrl.value = URL.createObjectURL(file);
}

// Typing a URL replaces a picked-but-not-yet-uploaded file.
function onImageUrlInput() {
  if (selectedImageFile.value) clearSelectedFile();
}

function clearImage() {
  clearSelectedFile();
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
      originalAttributes.value = { ...(p.attributes || {}) };

      const pCat = (p.attributes?.category as string) || "Poyabzal";
      if (!availableCategories.value.includes(pCat)) {
        availableCategories.value.unshift(pCat);
      }
      category.value = pCat;

      if (p.attributes) {
        material.value = (p.attributes.material as string) || "";
        fit.value = (p.attributes.fit as string) || "";
        gender.value = (p.attributes.gender as string) || "unisex";
        aiInstructions.value = (p.attributes.ai_instructions as string) || (p.attributes.ai_notes as string) || "";
      }

      serverImages.value = [...(p.images || [])];
      const primary = serverImages.value.find((img) => img.is_primary) ?? serverImages.value[0];
      originalPrimaryUrl.value = primary?.url ?? null;
      imageUrl.value = primary?.url ?? "";

      variants.value = (p.variants || []).map((v) => {
        const attrs = { ...(v.attributes || {}) } as Record<string, any>;
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
          image_url: v.image_url || (v.images && v.images.length > 0 ? v.images[0]! : null),
          images: v.images && v.images.length > 0 ? [...v.images] : (v.image_url ? [v.image_url] : []),
          new_image_url: "",
          availability: v.availability ?? true,
          uploading: 0,
        };
      });
    } catch (err) {
      error.value = t("productForm.fetchError");
    } finally {
      loading.value = false;
    }
  }
});

// --- Save --------------------------------------------------------------------

function isBlank(value: string | number | null | undefined): boolean {
  return value === null || value === undefined || String(value).trim() === "";
}

// Older versions of this form stored variant photos inline as data: URLs,
// which the server now rejects. Such photos are uploaded as files on save so
// an old product stays editable.
function dataUrlToFile(dataUrl: string, index: number): File | null {
  const match = /^data:([^;,]*)((?:;[^;,]*)*),(.*)$/s.exec(dataUrl);
  if (!match) return null;
  const mime = match[1] || "application/octet-stream";
  const isBase64 = /;base64/i.test(match[2] ?? "");
  const payload = match[3] ?? "";
  const bytes = isBase64
    ? Uint8Array.from(atob(payload), (ch) => ch.charCodeAt(0))
    : new TextEncoder().encode(decodeURIComponent(payload));
  const ext = mime.split("/")[1] || "bin";
  return new File([bytes], `variant-photo-${index}.${ext}`, { type: mime });
}

async function uploadInlineVariantImages() {
  let counter = 0;
  for (const v of variants.value) {
    const inline = v.images.filter((img) => img.startsWith("data:"));
    if (v.image_url?.startsWith("data:") && !inline.includes(v.image_url)) inline.push(v.image_url);
    for (const dataUrl of inline) {
      const file = dataUrlToFile(dataUrl, ++counter);
      const problem = file ? imageUploadProblem(file) : "type";
      if (!file || problem) throw new Error(t("productForm.legacyImageInvalid", { variant: v.value || "—" }));
      const { url } = await api.uploadProductMedia(file);
      v.images = v.images.map((img) => (img === dataUrl ? url : img));
      if (v.image_url === dataUrl) v.image_url = url;
    }
  }
}

function buildVariants(): VariantInput[] {
  return variants.value
    .filter((v) => v.color.trim() || v.size.trim() || v.value.trim())
    .map((v) => {
      const color = v.color.trim();
      const size = v.size.trim();
      const val = color && size ? `${color} / ${size}` : color || size || v.value.trim();

      const attrs: Record<string, any> = { ...v.attributes };
      if (color) attrs.color = color;
      if (size) attrs.size = size;

      const images = v.images.filter((img) => img.trim().length > 0);
      const primary = v.image_url?.trim() || images[0] || null;
      if (primary && images.includes(primary)) {
        images.splice(images.indexOf(primary), 1);
      }
      if (primary) images.unshift(primary);

      return {
        variant_type: "combination",
        value: val || "Standart",
        attributes: attrs,
        sku: v.sku?.trim() || null,
        barcode: v.barcode?.trim() || null,
        price_override: isBlank(v.price_override) ? null : Number(v.price_override),
        stock_quantity: isBlank(v.stock_quantity) ? null : Number(v.stock_quantity),
        image_url: primary,
        images,
        availability: v.availability,
      };
    });
}

// attributes.image_url mirrors the primary image (the server keeps it that
// way when images change) — set it to what the primary will be after save.
function buildAttributes(primaryUrl: string | null): Record<string, any> {
  const attrs: Record<string, any> = { ...originalAttributes.value };
  delete attrs.image_url;
  delete attrs.ai_notes;
  attrs.category = category.value || "Poyabzal";
  const optional: Record<string, string> = {
    material: material.value.trim(),
    fit: fit.value.trim(),
    gender: gender.value.trim(),
    ai_instructions: aiInstructions.value.trim(),
  };
  for (const [key, value] of Object.entries(optional)) {
    if (value) attrs[key] = value;
    else delete attrs[key];
  }
  if (primaryUrl) attrs.image_url = primaryUrl;
  return attrs;
}

/** Edit mode: the product's image list after save — the main photo as the
 * only primary, the other existing images kept, and the previous primary
 * dropped if the main photo was replaced or cleared. */
function desiredImages(): ImageInput[] {
  const main = imageUrl.value.trim() || null;
  const replaced = originalPrimaryUrl.value && originalPrimaryUrl.value !== main ? originalPrimaryUrl.value : null;
  const others = serverImages.value
    .filter((img) => img.url !== main && img.url !== replaced)
    .map((img) => ({ url: img.url, is_primary: false }));
  return main ? [{ url: main, is_primary: true }, ...others] : others;
}

function primaryOf(images: { url: string; is_primary: boolean }[]): string | null {
  return (images.find((img) => img.is_primary) ?? images[0])?.url ?? null;
}

// The server keeps rows for unchanged URLs and deletes removed ones, so
// `images` is only sent when the list actually changed.
function imagesChanged(desired: ImageInput[]): boolean {
  const current = serverImages.value;
  if (desired.length !== current.length) return true;
  const currentUrls = new Set(current.map((img) => img.url));
  if (desired.some((img) => !currentUrls.has(img.url))) return true;
  return primaryOf(desired) !== primaryOf(current);
}

function validate(): string | null {
  if (!name.value.trim()) return t("productForm.productNameRequired");
  if (!isBlank(price.value)) {
    const n = Number(price.value);
    if (!Number.isFinite(n) || n < 0) return t("productForm.priceInvalid");
  }
  for (const v of variants.value) {
    if (!isBlank(v.stock_quantity)) {
      const n = Number(v.stock_quantity);
      if (!Number.isInteger(n) || n < 0) return t("productForm.stockInvalid");
    }
    if (!isBlank(v.price_override)) {
      const n = Number(v.price_override);
      if (!Number.isFinite(n) || n < 0) return t("productForm.priceInvalid");
    }
  }
  const typedUrl = imageUrl.value.trim();
  if (typedUrl && !isHttpUrl(typedUrl)) return t("productForm.imageUrlInvalid");
  return null;
}

async function onSubmit() {
  // Ctrl+S (or a second click) while a save is running must not save twice.
  if (saving.value || loading.value) return;
  if (uploadingVariantImages.value) {
    error.value = t("productForm.waitForUploads");
    return;
  }
  // A URL typed into a variant but not yet added counts too.
  for (const v of variants.value) {
    if (v.new_image_url.trim() && !addSkuImage(v)) return;
  }
  const problem = validate();
  if (problem) {
    error.value = problem;
    return;
  }

  error.value = null;
  saving.value = true;

  // Each step leaves the form in a state where pressing Save again resumes
  // from where it failed: an uploaded file becomes the form's URL.
  try {
    await uploadInlineVariantImages();
    const base = {
      name: name.value.trim(),
      description: description.value.trim() || null,
      price: isBlank(price.value) ? null : Number(price.value),
      currency: currency.value,
      availability: availability.value,
      variants: buildVariants(),
    };

    if (props.mode === "create") {
      // A new product has no id to attach an image to yet: the file goes to
      // /products/media first and its URL is sent with the product.
      if (selectedImageFile.value) {
        const { url } = await api.uploadProductMedia(selectedImageFile.value);
        clearSelectedFile();
        imageUrl.value = url;
      }
      const main = imageUrl.value.trim() || null;
      const payload: ProductInput = {
        ...base,
        attributes: buildAttributes(main),
        images: main ? [{ url: main, is_primary: true }] : [],
      };
      await api.createProduct(payload);
      router.push("/products");
    } else if (props.mode === "edit" && props.productId) {
      if (selectedImageFile.value) {
        // Uploaded straight onto the product as its (only) primary image.
        const img = await api.uploadProductImage(props.productId, selectedImageFile.value, true);
        serverImages.value = [...serverImages.value.map((i) => ({ ...i, is_primary: false })), img];
        clearSelectedFile();
        imageUrl.value = img.url;
      }
      const images = desiredImages();
      const patch: ProductPatch = { ...base, attributes: buildAttributes(primaryOf(images)) };
      if (imagesChanged(images)) patch.images = images;
      await api.updateProduct(props.productId, patch);
      router.push("/products");
    }
  } catch (err) {
    error.value = errorText(err);
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
  if (imagePreviewUrl.value) URL.revokeObjectURL(imagePreviewUrl.value);
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
          :disabled="saving || loading || uploadingVariantImages || !name.trim()"
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
            <Switch v-model="availability" />
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
                min="0"
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
                @input="onImageUrlInput"
              />
            </div>
            <div class="space-y-1">
              <Label class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                {{ t("productForm.orUploadFile") }}
              </Label>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
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
                      {{ v.color || v.size ? [v.color, v.size].filter(Boolean).join(" • ") : t("productForm.newVariant") }}
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
                  v-model="v.color"
                  :placeholder="t('productForm.colorPlaceholder')"
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
                  v-model="v.size"
                  :placeholder="t('productForm.sizePlaceholder')"
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
                  <Loader2 v-if="v.uploading > 0" :size="13" class="text-primary animate-spin" />
                  <Upload v-else :size="13" class="text-primary" />
                  <span class="hidden sm:inline">{{ v.uploading > 0 ? t("productForm.uploading") : t("productForm.selectFile") }}</span>
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
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
          :disabled="saving || loading || uploadingVariantImages || !name.trim()"
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
