<script setup lang="ts">
definePageMeta({ layout: "default" });
</script>

<template>
  <div style="max-width: 760px; margin: 0 auto; padding: 3rem 1.5rem; line-height: 1.7">
    <div style="margin-bottom: 2rem; border-bottom: 1px solid var(--border); padding-bottom: 1.5rem;">
      <h1 style="font-size: 2rem; font-weight: 700; margin-bottom: 0.5rem;">Mivo AI — Privacy Policy</h1>
      <p class="muted">Last updated: August 2026</p>
    </div>

    <div style="background: rgba(14, 165, 233, 0.08); border: 1px solid rgba(14, 165, 233, 0.25); border-radius: 8px; padding: 1.25rem; margin-bottom: 2rem;">
      <h3 style="font-weight: 600; margin-top: 0; margin-bottom: 0.5rem; color: #0284c7;">Legal Entity & Business Information</h3>
      <p style="margin: 0.25rem 0;"><strong>Legal Business Entity:</strong> NASRIDDINOV XOJIAKBAR (Self-Employed Entrepreneur)</p>
      <p style="margin: 0.25rem 0;"><strong>Commercial Brand:</strong> Mivo AI</p>
      <p style="margin: 0.25rem 0;"><strong>Registration / PINFL:</strong> 52806065940056</p>
      <p style="margin: 0.25rem 0;"><strong>Registered Business Address:</strong> Obod MFY, Guliston ko'chasi, 5-uy, Uchqo'rg'on tumani, Namangan viloyati, 160100, Uzbekistan</p>
      <p style="margin: 0.25rem 0;"><strong>Official Contact Email:</strong> <a href="mailto:support@nasriddinov.dev" style="color: #0284c7; text-decoration: underline;">support@nasriddinov.dev</a></p>
      <p style="margin: 0.25rem 0;"><strong>Website:</strong> <a href="https://mivo.nasriddinov.dev" style="color: #0284c7; text-decoration: underline;">https://mivo.nasriddinov.dev</a></p>
    </div>

    <p>
      <strong>NASRIDDINOV XOJIAKBAR</strong> operating as <strong>Mivo AI</strong> ("we", "our", "us") provides an autonomous AI sales assistant and customer relationship platform designed for businesses selling through Instagram Direct Messages. This Privacy Policy explains what data we collect, why, and how it is securely handled.
    </p>

    <h2 style="font-size: 1.35rem; font-weight: 600; margin-top: 2rem; margin-bottom: 0.75rem;">1. Information We Collect</h2>
    <ul>
      <li><strong>Business account data</strong>: Name, email address, password hash, and AI selling settings configured by the business owner.</li>
      <li><strong>Instagram Integration data</strong>: Connected Instagram Professional / Business account ID, access tokens (encrypted at rest), account username, and Direct Message inquiries exchanged between customers and the business account to allow the AI to generate relevant responses.</li>
      <li><strong>Customer data</strong>: Instagram-scoped user ID, username, and contact phone number if voluntarily provided by the customer during the conversation for order placement and sales qualification.</li>
      <li><strong>Product Catalog data</strong>: Product titles, prices, descriptions, images, and inventory stock availability uploaded by the business.</li>
      <li><strong>Telegram Integration data</strong>: Telegram chat ID used solely to deliver real-time hot lead alerts to the business owner.</li>
    </ul>

    <h2 style="font-size: 1.35rem; font-weight: 600; margin-top: 2rem; margin-bottom: 0.75rem;">2. How We Use Information</h2>
    <ul>
      <li>To generate automated, grounded AI responses to customer product inquiries in Instagram Direct on behalf of the business.</li>
      <li>To qualify buying intent and immediately notify the business owner of hot sales opportunities.</li>
      <li>To provide the centralized CRM dashboard for managing products, conversations, and sales leads.</li>
    </ul>
    <p>We do not sell, rent, or monetize your data or your customers' data to any third parties or data brokers.</p>

    <h2 style="font-size: 1.35rem; font-weight: 600; margin-top: 2rem; margin-bottom: 0.75rem;">3. Third-Party Service Providers</h2>
    <ul>
      <li><strong>Meta Platforms, Inc. / Instagram Graph API</strong> — Primary messaging channel and webhook delivery.</li>
      <li><strong>OpenRouter / LLM Providers</strong> — Natural language processing to generate catalog-grounded sales replies.</li>
      <li><strong>Cloudflare R2</strong> — Secure cloud object storage for product images.</li>
      <li><strong>Telegram API</strong> — Direct delivery of urgent sales lead notifications to business operators.</li>
    </ul>

    <h2 style="font-size: 1.35rem; font-weight: 600; margin-top: 2rem; margin-bottom: 0.75rem;">4. Data Retention & User Rights</h2>
    <p>
      Business data is retained as long as your account remains active. You may request full deletion of your account, integration tokens, and all associated conversation history at any time by emailing us at <a href="mailto:support@nasriddinov.dev" style="color: #0284c7; text-decoration: underline;">support@nasriddinov.dev</a>.
    </p>

    <h2 style="font-size: 1.35rem; font-weight: 600; margin-top: 2rem; margin-bottom: 0.75rem;">5. Security Standards</h2>
    <p>
      All OAuth tokens and sensitive credentials are encrypted at rest using industry-standard cryptography. All network communications are encrypted in transit via TLS/HTTPS. Multi-tenant isolation ensures each business's data is strictly separated.
    </p>

    <h2 style="font-size: 1.35rem; font-weight: 600; margin-top: 2rem; margin-bottom: 0.75rem;">6. Contact Information & Data Controller</h2>
    <p>
      For privacy inquiries, data deletion requests, or compliance questions, please contact:
    </p>
    <p style="margin-left: 1rem;">
      <strong>NASRIDDINOV XOJIAKBAR (Mivo AI)</strong><br />
      Address: Obod MFY, Guliston ko'chasi, 5-uy, Uchqo'rg'on tumani, Namangan viloyati, 160100, Uzbekistan<br />
      Email: <a href="mailto:support@nasriddinov.dev" style="color: #0284c7; text-decoration: underline;">support@nasriddinov.dev</a><br />
      Website: <a href="https://mivo.nasriddinov.dev" style="color: #0284c7; text-decoration: underline;">https://mivo.nasriddinov.dev</a>
    </p>
  </div>
</template>
