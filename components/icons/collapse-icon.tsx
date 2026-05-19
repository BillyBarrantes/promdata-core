// components/icons/collapse-icon.tsx
import React from 'react';

export const CollapseIcon = ({ className }: { className?: string }) => {
  return (
    <svg 
      width="20" 
      height="17" 
      viewBox="0 0 20 17" 
      fill="none" 
      xmlns="http://www.w3.org/2000/svg"
      className={className} // Permite pasarle clases para cambiar tamaño, etc.
    >
      <path 
        d="M9 16L1 8.5L9 1" 
        stroke="currentColor" // Cambiado de "black" a "currentColor"
        strokeWidth="2" 
        strokeLinecap="round" 
        strokeLinejoin="round"
      />
      <path 
        d="M19 16L11 8.5L19 1" 
        stroke="currentColor" // Cambiado de "black" a "currentColor"
        strokeWidth="2" 
        strokeLinecap="round" 
        strokeLinejoin="round"
      />
    </svg>
  );
};