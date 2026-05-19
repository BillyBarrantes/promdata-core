// components/icons/save-icon.tsx
import React from 'react';

export const SaveIcon = ({ className }: { className?: string }) => {
  return (
    <svg 
      width="27" 
      height="27" 
      viewBox="0 0 27 27" 
      fill="none" 
      xmlns="http://www.w3.org/2000/svg" 
      className={className}
    >
      <path 
        d="M6.46875 24.4375V17.4062C6.46875 16.5433 7.16831 15.8438 8.03125 15.8438H18.9688C19.8317 15.8438 20.5312 16.5433 20.5312 17.4062V25.2188M20.5312 2.5625V5.6875C20.5312 6.55044 19.8317 7.25 18.9688 7.25L8.03125 7.25C7.1683 7.25 6.46875 6.55044 6.46875 5.6875L6.46875 1M25.2155 6.46546L20.5345 1.78454C20.0322 1.28221 19.3509 1 18.6405 1H3.67857C2.19922 1 1 2.19922 1 3.67857V23.3214C1 24.8008 2.19922 26 3.67857 26H23.3214C24.8008 26 26 24.8008 26 23.3214V8.35949C26 7.64909 25.7178 6.96778 25.2155 6.46546Z" 
        stroke="currentColor" 
        strokeWidth="2" 
        strokeLinecap="round"
      />
    </svg>
  );
};